/*
 * argv-scrub — LD_PRELOAD shim that keeps the RCON password out of the
 * process list.
 *
 * Factorio only accepts the RCON password as `--rcon-password <pw>`, and
 * argv is world-readable through /proc/<pid>/cmdline (ps, top, ...). This
 * shim interposes __libc_start_main, hands the program private heap copies
 * of argv (so the program still sees the real password), and zeroes the
 * original argument strings that /proc/<pid>/cmdline reads. The password is
 * visible only for the moment between execve() and the dynamic loader
 * reaching this shim.
 *
 * Installed to /usr/local/lib/factorio/libargvscrub.so by the factorio role;
 * only used by factorio-launch. Fails open on a statically linked binary
 * (the interposer never runs), which the role's post-start check catches.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdlib.h>
#include <string.h>

typedef int (*main_fn)(int, char **, char **);
typedef int (*start_main_fn)(main_fn, int, char **, void (*)(void),
                             void (*)(void), void (*)(void), void *);

static main_fn real_main;

static int scrubbing_main(int argc, char **argv, char **envp)
{
    char **copy = calloc((size_t)argc + 1, sizeof *copy);
    if (copy == NULL)
        abort(); /* never run with the secret still exposed */

    for (int i = 0; i < argc; i++) {
        copy[i] = strdup(argv[i]);
        if (copy[i] == NULL)
            abort();
    }
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i - 1], "--rcon-password") == 0)
            memset(argv[i], 0, strlen(argv[i]));
    }
    return real_main(argc, copy, envp);
}

int __libc_start_main(main_fn main, int argc, char **argv, void (*init)(void),
                      void (*fini)(void), void (*rtld_fini)(void),
                      void *stack_end)
{
    start_main_fn real = (start_main_fn)dlsym(RTLD_NEXT, "__libc_start_main");
    real_main = main;
    return real(scrubbing_main, argc, argv, init, fini, rtld_fini, stack_end);
}
