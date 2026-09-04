/* Minimal Mach-O launcher for the .app bundle.
 * macOS 26 refuses to launch bundles whose CFBundleExecutable is a shell
 * script, so this tiny binary stands in and execs the venv Python.
 * Paths are baked in at compile time by make_app.sh. */
#include <stdlib.h>
#include <unistd.h>

int main(void) {
    chdir(PROJECT_DIR);
    if (access(PYTHON_BIN, X_OK) != 0) {
        /* No virtualenv yet -- first run after a clone, or it was deleted.
         * bootstrap.sh picks a Python 3.10+ interpreter (macOS only ships
         * 3.9, which yt-dlp has deprecated) and reports its own errors. */
        system("/usr/bin/osascript -e 'display notification \"Setting up, one moment…\""
               " with title \"Grabby\"'");
        if (system("'" PROJECT_DIR "/bootstrap.sh'") != 0) return 1;
    }
    execl(PYTHON_BIN, PYTHON_BIN, SCRIPT, (char *)NULL);
    return 1;
}
