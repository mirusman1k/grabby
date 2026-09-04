/* Minimal Mach-O launcher for the .app bundle.
 * macOS 26 refuses to launch bundles whose CFBundleExecutable is a shell
 * script, so this tiny binary stands in and execs the venv Python.
 * Paths are baked in at compile time by make_app.sh. */
#include <stdlib.h>
#include <unistd.h>

int main(void) {
    chdir(PROJECT_DIR);
    if (access(PYTHON_BIN, X_OK) != 0) {   /* first run: build the venv */
        system("/usr/bin/osascript -e 'display notification \"Setting up, one moment…\""
               " with title \"Grabby\"'");
        system("/usr/bin/python3 -m venv '" PROJECT_DIR "/.venv' && "
               "'" PROJECT_DIR "/.venv/bin/pip' install -q -r '"
               PROJECT_DIR "/requirements.txt' pywebview");
    }
    execl(PYTHON_BIN, PYTHON_BIN, SCRIPT, (char *)NULL);
    return 1;
}
