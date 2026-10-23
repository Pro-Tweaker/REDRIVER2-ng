package io.github.opendriver.redriver2;

import org.libsdl.app.SDLActivity;

public class RedriverActivity extends SDLActivity {
    @Override
    protected String[] getLibraries() {
        return new String[] {
            "c++_shared",
            "SDL2",
            "openal",
            "main"
        };
    }
}
