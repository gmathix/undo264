//
// Created by gmathix on 10/29/26.
//

/*
 * replay_main.c — minimal stand-in for libFuzzer's main().
 *
 * Calls LLVMFuzzerTestOneInput() once per file given on the command line
 * (directories are walked one level deep, so a whole corpus can be passed).
 * No mutation, no instrumentation requirement, no clang requirement.
 *
 * Used for:
 *   - coverage reports (llvm-cov source-based coverage)
 *   - debugging a crash in CLion / gdb / lldb with a plain, predictable main()
 */
#include <dirent.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

extern int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size);

static int run_file(const char *path) {
    FILE *f = fopen(path, "rb");
    if (!f) { perror(path); return -1; }
    fseek(f, 0, SEEK_END);
    long len = ftell(f);
    fseek(f, 0, SEEK_SET);
    /* malloc(len) exactly, so ASan sees the true input bounds like libFuzzer does */
    uint8_t *buf = malloc(len > 0 ? (size_t)len : 1);
    if (!buf) { fclose(f); return -1; }
    size_t got = fread(buf, 1, (size_t)len, f);
    fclose(f);
    fprintf(stderr, "Running: %s (%zu bytes)\n", path, got);
    LLVMFuzzerTestOneInput(buf, got);
    free(buf);
    return 0;
}

static void run_path(const char *path) {
    struct stat st;
    if (stat(path, &st) != 0) { perror(path); return; }
    if (!S_ISDIR(st.st_mode)) { run_file(path); return; }
    DIR *d = opendir(path);
    if (!d) { perror(path); return; }
    struct dirent *e;
    while ((e = readdir(d))) {
        if (e->d_name[0] == '.') continue;
        char full[4096];
        snprintf(full, sizeof full, "%s/%s", path, e->d_name);
        if (stat(full, &st) == 0 && S_ISREG(st.st_mode)) run_file(full);
    }
    closedir(d);
}

int main(int argc, char **argv) {
    if (argc < 2) { fprintf(stderr, "usage: %s <file|dir>...\n", argv[0]); return 2; }
    for (int i = 1; i < argc; i++) run_path(argv[i]);
    return 0;
}