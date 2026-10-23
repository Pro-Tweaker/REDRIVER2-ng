import os
import struct
import tempfile
import unittest
import zipfile

import cd2obb


def raw_sector(data, form2=False):
    """MODE2/2352 sector: sync + header + subheader x2 + user data + EDC/ECC."""
    submode = 0x20 if form2 else 0x08
    sub = bytes([0, 0, submode, 0]) * 2
    user = data.ljust(2048, b'\x00')
    return cd2obb.SYNC + b'\x00\x02\x00\x02' + sub + user + b'\x00' * 280


def dir_record(name, lba, size, is_dir):
    name_b = name.encode('ascii')
    length = 33 + len(name_b)
    if length % 2:
        length += 1
    rec = bytearray(length)
    rec[0] = length
    struct.pack_into('<I', rec, 2, lba)
    struct.pack_into('>I', rec, 6, lba)
    struct.pack_into('<I', rec, 10, size)
    struct.pack_into('>I', rec, 14, size)
    rec[25] = 2 if is_dir else 0
    rec[32] = len(name_b)
    rec[33:33 + len(name_b)] = name_b
    return bytes(rec)


def dir_extent(entries):
    dot = dir_record('\x00', 0, 2048, True)
    dotdot = dir_record('\x01', 0, 2048, True)
    return (dot + dotdot + b''.join(entries)).ljust(2048, b'\x00')


def build_image(path, files, streams):
    """files: {'DRIVER2/DATA/NAME': bytes}, streams: {'MOVIE.STR': bytes} placed in the root."""
    sectors = {}
    next_lba = 20

    def alloc(data, form2=False):
        nonlocal next_lba
        lba = next_lba
        count = max(1, (len(data) + 2047) // 2048)
        for i in range(count):
            sectors[lba + i] = raw_sector(data[i * 2048:(i + 1) * 2048], form2)
        next_lba += count
        return lba

    file_recs = []
    for name, data in files.items():
        file_recs.append(dir_record(os.path.basename(name) + ';1', alloc(data), len(data), False))

    stream_recs = []
    for name, data in streams.items():
        stream_recs.append(dir_record(name + ';1', alloc(data, form2=True), len(data), False))

    data_dir = alloc(dir_extent(file_recs))
    driver_dir = alloc(dir_extent([dir_record('DATA', data_dir, 2048, True)]))
    root_dir = alloc(dir_extent([dir_record('DRIVER2', driver_dir, 2048, True)] + stream_recs))

    pvd = bytearray(2048)
    pvd[0:6] = b'\x01CD001'
    pvd[156:156 + 34] = dir_record('\x00', root_dir, 2048, True)[:34]
    sectors[16] = raw_sector(bytes(pvd))
    sectors[17] = raw_sector(b'\xff' + b'CD001' + b'\x01')

    with open(path, 'wb') as f:
        for lba in range(next_lba):
            f.write(sectors.get(lba, raw_sector(b'')))


class Cd2ObbTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bin = os.path.join(self.tmp.name, 'cd1.bin')
        self.payload = bytes(range(256)) * 20          # 5120 bytes, spans 3 sectors
        build_image(self.bin,
                    {'DRIVER2/DATA/CITY.BIN': self.payload, 'DRIVER2/DATA/SMALL.TXT': b'hello'},
                    {'INTRO.STR': b'\xaa' * 4096})

    def run_tool(self, *extra):
        out = os.path.join(self.tmp.name, 'out.obb')
        rc = cd2obb.main([self.bin, '-o', out, '-q', '--no-repo-data'] + list(extra))
        return rc, out

    def test_extracts_files_and_skips_streams(self):
        rc, out = self.run_tool('--all-files')
        self.assertEqual(rc, 0)
        with zipfile.ZipFile(out) as z:
            names = set(z.namelist())
            self.assertIn('DRIVER2/DRIVER2/DATA/CITY.BIN', names)
            self.assertEqual(z.read('DRIVER2/DRIVER2/DATA/CITY.BIN'), self.payload)
            self.assertEqual(z.read('DRIVER2/DRIVER2/DATA/SMALL.TXT'), b'hello')
            self.assertFalse(any('INTRO' in n for n in names))

    def test_default_keeps_only_game_folder(self):
        rc, out = self.run_tool()
        self.assertEqual(rc, 0)
        with zipfile.ZipFile(out) as z:
            self.assertEqual(sorted(z.namelist()), ['DRIVER2/DATA/CITY.BIN', 'DRIVER2/DATA/SMALL.TXT'])
            self.assertEqual(z.read('DRIVER2/DATA/CITY.BIN'), self.payload)

    def test_extra_folder_is_merged(self):
        extra = os.path.join(self.tmp.name, 'conv', 'DRIVER2', 'FMV', '1')
        os.makedirs(extra)
        with open(os.path.join(extra, 'RENDER1.STR[0].avi'), 'wb') as f:
            f.write(b'avi')

        rc, out = self.run_tool('--extra', os.path.join(self.tmp.name, 'conv'))
        self.assertEqual(rc, 0)
        with zipfile.ZipFile(out) as z:
            self.assertEqual(z.read('DRIVER2/FMV/1/RENDER1.STR[0].avi'), b'avi')

    def test_cue_sheet(self):
        cue = os.path.join(self.tmp.name, 'cd1.cue')
        with open(cue, 'w') as f:
            f.write('FILE "cd1.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n')
        out = os.path.join(self.tmp.name, 'cue.obb')
        self.assertEqual(cd2obb.main([cue, '-o', out, '-q', '--no-repo-data']), 0)

    def test_not_a_disc(self):
        bad = os.path.join(self.tmp.name, 'bad.bin')
        with open(bad, 'wb') as f:
            f.write(b'\x00' * 100000)
        with self.assertRaises(ValueError):
            cd2obb.main([bad, '-o', os.path.join(self.tmp.name, 'x.obb'), '-q'])


if __name__ == '__main__':
    unittest.main()
