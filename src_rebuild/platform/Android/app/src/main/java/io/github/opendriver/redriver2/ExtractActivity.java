package io.github.opendriver.redriver2;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Color;
import android.os.Bundle;
import android.view.Gravity;
import android.view.ViewGroup;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.util.ArrayList;
import java.util.Enumeration;
import java.util.List;
import java.util.zip.ZipEntry;
import java.util.zip.ZipFile;

/**
 * Launcher activity. Unpacks the expansion file made by tools/cd2obb into the app's
 * external files folder (where the game looks for 'DRIVER2/') on first run, then starts the game.
 *
 * The OBB is read from getObbDir(): /sdcard/Android/obb/&lt;package&gt;/main.1.&lt;package&gt;.obb
 */
public class ExtractActivity extends Activity {
    private static final String MARKER = ".obb_extracted";

    private TextView statusText;
    private ProgressBar progressBar;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        layout.setGravity(Gravity.CENTER);
        layout.setBackgroundColor(Color.BLACK);
        layout.setPadding(48, 48, 48, 48);

        statusText = new TextView(this);
        statusText.setTextColor(Color.WHITE);
        statusText.setTextSize(16);
        statusText.setGravity(Gravity.CENTER);
        layout.addView(statusText, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        progressBar = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progressBar.setMax(1000);
        layout.addView(progressBar, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        setContentView(layout);

        final File filesDir = getExternalFilesDir(null);
        if (filesDir == null) {
            showMessage("External storage is not available.");
            return;
        }

        final File obb = findObb();
        final File gameData = new File(filesDir, "DRIVER2");

        if (obb == null) {
            if (gameData.isDirectory()) {
                startGame();   // files were copied by hand
            } else {
                showMessage("Game data not found.\n\nCopy the .obb made by cd2obb to:\n" + getObbDir()
                        + "\n\nor copy the DRIVER2 folder to:\n" + filesDir);
            }
            return;
        }

        if (isExtracted(filesDir, obb) && gameData.isDirectory()) {
            startGame();
            return;
        }

        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    extract(obb, filesDir);
                    runOnUiThread(new Runnable() {
                        @Override
                        public void run() {
                            startGame();
                        }
                    });
                } catch (final Exception e) {
                    runOnUiThread(new Runnable() {
                        @Override
                        public void run() {
                            showMessage("Failed to unpack " + obb.getName() + ":\n" + e);
                        }
                    });
                }
            }
        }, "obb-extract").start();
    }

    private void showMessage(String text) {
        statusText.setText(text);
        progressBar.setVisibility(ViewGroup.GONE);
    }

    private void startGame() {
        startActivity(new Intent(this, RedriverActivity.class));
        finish();
    }

    private File findObb() {
        File dir = getObbDir();
        File[] files = dir == null ? null : dir.listFiles();
        if (files == null)
            return null;

        File best = null;
        for (File f : files) {
            if (f.isFile() && f.getName().endsWith(".obb") && (best == null || f.getName().compareTo(best.getName()) > 0))
                best = f;   // highest version wins
        }
        return best;
    }

    private static String signature(File obb) {
        return obb.getName() + ":" + obb.length() + ":" + obb.lastModified();
    }

    private boolean isExtracted(File filesDir, File obb) {
        File marker = new File(filesDir, MARKER);
        if (!marker.isFile())
            return false;

        try (InputStream in = new FileInputStream(marker)) {
            byte[] buf = new byte[512];
            int n = in.read(buf);
            return n > 0 && new String(buf, 0, n, "UTF-8").equals(signature(obb));
        } catch (IOException e) {
            return false;
        }
    }

    private void extract(File obb, File filesDir) throws IOException {
        File marker = new File(filesDir, MARKER);
        marker.delete();   // an interrupted extraction must not look finished

        final String base = filesDir.getCanonicalPath() + File.separator;

        try (ZipFile zip = new ZipFile(obb)) {
            List<ZipEntry> entries = new ArrayList<>();
            long total = 0;
            for (Enumeration<? extends ZipEntry> en = zip.entries(); en.hasMoreElements(); ) {
                ZipEntry e = en.nextElement();
                if (!e.isDirectory()) {
                    entries.add(e);
                    total += Math.max(e.getSize(), 0);
                }
            }

            long done = 0;
            byte[] buf = new byte[1 << 16];

            for (ZipEntry e : entries) {
                File out = new File(filesDir, e.getName());
                if (!out.getCanonicalPath().startsWith(base))
                    throw new IOException("Bad path in archive: " + e.getName());   // zip slip

                File parent = out.getParentFile();
                if (parent != null && !parent.isDirectory() && !parent.mkdirs())
                    throw new IOException("Cannot create " + parent);

                setProgress(e.getName(), done, total);

                try (InputStream in = zip.getInputStream(e); OutputStream os = new FileOutputStream(out)) {
                    int n;
                    while ((n = in.read(buf)) > 0) {
                        os.write(buf, 0, n);
                        done += n;
                    }
                }
                setProgress(e.getName(), done, total);
            }
        }

        try (OutputStream os = new FileOutputStream(marker)) {
            os.write(signature(obb).getBytes("UTF-8"));
        }
    }

    private long lastUpdate;

    private void setProgress(final String name, final long done, final long total) {
        long now = System.currentTimeMillis();
        if (now - lastUpdate < 100 && done < total)
            return;
        lastUpdate = now;

        runOnUiThread(new Runnable() {
            @Override
            public void run() {
                statusText.setText("Unpacking game data (first run only)\n" + name);
                progressBar.setProgress(total > 0 ? (int) (done * 1000 / total) : 0);
            }
        });
    }
}
