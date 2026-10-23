#!/usr/bin/env python3
"""
cd2obb - packs a Driver 2 CD image into an Android expansion file (.obb) for REDRIVER2.

The game looks for loose files in the 'DRIVER2' folder first and falls back to the
CD image, so the CD contents are extracted into that layout:

    DRIVER2/DATA/...    (copied from the CD, the ';1' version suffix is dropped)
    DRIVER2/FMV/...     (optional, converted movies - see --convert-media / --extra)
    DRIVER2/XA/...      (optional, converted audio)

The result is a ZIP archive (what the Android app unpacks on first launch) named like
Play Store expansion files: main.<version>.<package>.obb

Needs only Python 3.7+. Movies/audio (STR and XA streams) are not stored on the disc as
normal files, they are skipped here and have to be converted with jPSXdec, see --convert-media.

Usage:
    cd2obb.py Driver2CD1.bin                       # -> main.1.io.github.opendriver.redriver2.obb
                                                   #    (Disc 1 has all the game data, Disc 2 only adds movies)
    cd2obb.py Driver2CD1.cue -o out.obb
    cd2obb.py Driver2CD1.bin --extra path/to/DRIVER2   # merge already converted FMV/XA/etc.
    cd2obb.py Driver2CD1.bin Driver2CD2.bin --convert-media --jpsxdec jpsxdec.jar
"""

import argparse
import os
import re
import struct
import subprocess
import sys
import tempfile
import zipfile

PACKAGE = 'io.github.opendriver.redriver2'
GAME_FOLDER = 'DRIVER2'

SECTOR_USER = 2048
PVD_SECTOR = 16

# raw sector layouts: (sector size, offset of the 2048 user bytes of a Form 1 sector)
RAW_LAYOUTS = {
    2352: 24,   # MODE2/2352: sync(12) + header(4) + subheader(8)
    2336: 8,    # MODE2/2336: subheader(8)
    2048: 0,    # cooked ISO
}

SYNC = b'\x00\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\x00'


class CDImage:
    """Read-only access to the user data of a CD image (.bin / .iso)."""

    def __init__(self, path):
        self.path = path
        self.f = open(path, 'rb')
        self.f.seek(0, os.SEEK_END)
        self.size = self.f.tell()
        try:
            self.sector_size = self._detect_sector_size()
        except Exception:
            self.f.close()
            raise
        self.user_offset = RAW_LAYOUTS[self.sector_size]

    def close(self):
        self.f.close()

    def _detect_sector_size(self):
        for size in (2352, 2336, 2048):
            for off in (RAW_LAYOUTS[size],):
                self.f.seek(PVD_SECTOR * size + off)
                pvd = self.f.read(6)
                if pvd == b'\x01CD001':
                    return size
        raise ValueError('%s: no ISO 9660 volume descriptor found (not a data track image?)' % self.path)

    @property
    def num_sectors(self):
        return self.size // self.sector_size

    def submode(self, lba):
        """Subheader submode byte of a raw sector, None for cooked images."""
        if self.sector_size == 2048:
            return None

        base = lba * self.sector_size + (16 if self.sector_size == 2352 else 0)
        self.f.seek(base + 2)
        return self.f.read(1)[0]

    def is_form2(self, lba):
        sm = self.submode(lba)
        return sm is not None and bool(sm & 0x20)

    def read_sector(self, lba):
        self.f.seek(lba * self.sector_size + self.user_offset)
        return self.f.read(SECTOR_USER)


class IsoEntry:
    def __init__(self, name, lba, size, is_dir):
        self.name = name
        self.lba = lba
        self.size = size
        self.is_dir = is_dir


def parse_dir_records(data):
    pos = 0
    while pos < len(data):
        length = data[pos]
        if length == 0:
            # records do not cross sector boundaries
            pos = (pos // SECTOR_USER + 1) * SECTOR_USER
            continue

        rec = data[pos:pos + length]
        pos += length

        lba = struct.unpack_from('<I', rec, 2)[0]
        size = struct.unpack_from('<I', rec, 10)[0]
        flags = rec[25]
        name_len = rec[32]
        name = rec[33:33 + name_len]

        if name in (b'\x00', b'\x01'):
            continue

        yield IsoEntry(name.decode('ascii', 'replace'), lba, size, bool(flags & 2))


def read_extent(img, lba, size):
    count = (size + SECTOR_USER - 1) // SECTOR_USER
    return b''.join(img.read_sector(lba + i) for i in range(count))[:size]


def read_dir(img, entry_lba, entry_size):
    return list(parse_dir_records(read_extent(img, entry_lba, entry_size)))


def root_directory(img):
    pvd = img.read_sector(PVD_SECTOR)
    # root directory record is at offset 156
    lba = struct.unpack_from('<I', pvd, 156 + 2)[0]
    size = struct.unpack_from('<I', pvd, 156 + 10)[0]
    return lba, size


def walk(img, lba, size, prefix=''):
    for e in read_dir(img, lba, size):
        path = prefix + clean_name(e.name)
        if e.is_dir:
            yield from walk(img, e.lba, e.size, path + '/')
        else:
            yield path, e


def clean_name(name):
    return re.sub(r';\d+$', '', name)


def is_stream_file(img, entry):
    """STR / XA files are stored as Form 2 sectors, they cannot be read as plain data."""
    return entry.size > 0 and img.is_form2(entry.lba)


def add_cd(zf, img, only_root_folder, log, seen, keep_str=False):
    root_lba, root_size = root_directory(img)

    stored = skipped = 0
    for path, e in walk(img, root_lba, root_size):
        if only_root_folder:
            if not path.upper().startswith(GAME_FOLDER + '/'):
                continue
            arc_path = GAME_FOLDER + path[len(GAME_FOLDER):]
        else:
            arc_path = GAME_FOLDER + '/' + path

        if not keep_str and arc_path.upper().endswith('.STR'):
            log('  skip   %s (raw movie, the game plays converted .avi)' % path)
            skipped += 1
            continue

        if arc_path in seen:
            log('  dup    %s (already taken from an earlier disc)' % arc_path)
            continue

        if is_stream_file(img, e):
            log('  skip   %s (stream, needs --convert-media)' % path)
            skipped += 1
            continue

        seen.add(arc_path)
        info = zipfile.ZipInfo(arc_path)
        info.compress_type = zipfile.ZIP_DEFLATED
        with zf.open(info, 'w') as out:
            remaining = e.size
            lba = e.lba
            while remaining > 0:
                chunk = img.read_sector(lba)[:min(SECTOR_USER, remaining)]
                out.write(chunk)
                remaining -= len(chunk)
                lba += 1

        log('  add    %s (%d bytes)' % (arc_path, e.size))
        stored += 1

    return stored, skipped


def add_folder(zf, folder, log, seen, root_name=GAME_FOLDER):
    """Adds loose files (converted media, user extras). Layout inside is relative to DRIVER2."""
    # allow pointing either at the DRIVER2 folder itself or at its parent
    base = folder
    if os.path.isdir(os.path.join(folder, GAME_FOLDER)):
        base = os.path.join(folder, GAME_FOLDER)

    count = 0
    for dirpath, _, files in os.walk(base):
        for name in files:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, base).replace(os.sep, '/')
            arc_path = '%s/%s' % (root_name, rel)

            if arc_path in seen:
                log('  dup    %s' % arc_path)
                continue
            seen.add(arc_path)

            # media is already compressed
            compress = zipfile.ZIP_STORED if name.lower().endswith(('.avi', '.wav', '.ogg')) else zipfile.ZIP_DEFLATED
            zf.write(full, arc_path, compress_type=compress)
            log('  add    %s' % arc_path)
            count += 1
    return count


def convert_media(images, jpsxdec, workdir, log):
    """Same steps as data/install/conv.sh: index, then convert video and audio."""
    for img_path in images:
        log('jPSXdec: %s' % img_path)
        index = os.path.join(workdir, 'index.idx')
        for args in (
            ['-f', img_path, '-x', index],
            ['-x', index, '-a', 'video', '-quality', 'psx', '-vf', 'avi:mjpg', '-up', 'Lanczos3'],
            ['-x', index, '-a', 'audio', '-quality', 'psx', '-af', 'wav'],
        ):
            cmd = ['java', '-jar', jpsxdec] + args
            log('  ' + ' '.join(cmd))
            subprocess.run(cmd, cwd=workdir, check=True)


def resolve_image_path(path):
    """Accepts .cue sheets, returns the .bin they point at."""
    if path.lower().endswith('.cue'):
        with open(path, 'r', errors='replace') as f:
            m = re.search(r'FILE\s+"?([^"\r\n]+?)"?\s+BINARY', f.read(), re.I)
        if not m:
            raise ValueError('%s: could not find the BIN file in the cue sheet' % path)
        return os.path.join(os.path.dirname(os.path.abspath(path)), m.group(1))
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description='Pack Driver 2 CD images into an Android .obb for REDRIVER2')
    ap.add_argument('images', nargs='+', help='CD image(s): .bin/.iso/.cue. First one is CD1, which holds the game data')
    ap.add_argument('-o', '--output', help='output file (default: main.<version>.%s.obb)' % PACKAGE)
    ap.add_argument('--version', type=int, default=1, help='expansion file version (default 1)')
    ap.add_argument('--extra', action='append', default=[], metavar='DIR',
                    help='folder with loose files to add (converted FMV/XA, replays...). Can be repeated')
    ap.add_argument('--convert-media', action='store_true', help='convert FMV and XA audio with jPSXdec (needs Java)')
    ap.add_argument('--jpsxdec', default=None, help='path to jpsxdec.jar (default: data/install/jpsxdec.jar of the repo)')
    ap.add_argument('--all-files', action='store_true',
                    help="store the whole disc, not only the '%s' folder" % GAME_FOLDER)
    ap.add_argument('--keep-str', action='store_true', help='also store the raw .STR movies (not used by the game, they are big)')
    ap.add_argument('--no-repo-data', action='store_true',
                    help="do not add the repository's data/%s folder (fonts, language texts, sound banks)" % GAME_FOLDER)
    ap.add_argument('-q', '--quiet', action='store_true')
    args = ap.parse_args(argv)

    log = (lambda s: None) if args.quiet else (lambda s: print(s, file=sys.stderr))

    images = [resolve_image_path(p) for p in args.images]
    for p in images:
        if not os.path.isfile(p):
            ap.error('image not found: %s' % p)

    output = args.output or 'main.%d.%s.obb' % (args.version, PACKAGE)

    extra_dirs = list(args.extra)

    # files the game needs that are not on the disc (fonts, LANG texts, XA sound banks...)
    repo_data = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'data')
    if not args.no_repo_data and os.path.isdir(os.path.join(repo_data, GAME_FOLDER)):
        extra_dirs.append(os.path.normpath(repo_data))
    tmp = None
    if args.convert_media:
        jpsxdec = args.jpsxdec or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                               '..', '..', 'data', 'install', 'jpsxdec.jar')
        if not os.path.isfile(jpsxdec):
            ap.error('jpsxdec.jar not found: %s' % jpsxdec)

        tmp = tempfile.TemporaryDirectory(prefix='cd2obb_')
        convert_media([os.path.abspath(p) for p in images], os.path.abspath(jpsxdec), tmp.name, log)
        extra_dirs.append(tmp.name)

    total_added = 0
    seen = set()
    with zipfile.ZipFile(output + '.tmp', 'w', allowZip64=True) as zf:
        for i, path in enumerate(images):
            img = CDImage(path)
            try:
                log('CD%d: %s (%d byte sectors)' % (i + 1, path, img.sector_size))
                stored, skipped = add_cd(zf, img, not args.all_files, log, seen, args.keep_str)
                log('  %d files stored, %d streams skipped' % (stored, skipped))
                total_added += stored
            finally:
                img.close()

        for d in extra_dirs:
            log('extra: %s' % d)
            total_added += add_folder(zf, d, log, seen)

    if total_added == 0:
        os.remove(output + '.tmp')
        print("error: nothing was added. Is there a '%s' folder on the disc?" % GAME_FOLDER, file=sys.stderr)
        return 1

    os.replace(output + '.tmp', output)
    if tmp:
        tmp.cleanup()

    print('%s: %d files, %.1f MB' % (output, total_added, os.path.getsize(output) / 1048576.0))
    return 0


if __name__ == '__main__':
    sys.exit(main())
