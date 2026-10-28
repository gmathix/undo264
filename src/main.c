//
// Created by gmathix on 3/18/26.
//


#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdbool.h>

#include "decoder.h"

#ifdef UNIX
#include <unistd.h>
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#endif

#ifdef WIN32
#include <Windows.h>
#endif

#include "tests/profiler.h"



static void print_usage(void) {
    fprintf(stderr,
        "Usage: undo264 -i <input> [OPTIONS]\n\n"
        "Options:\n"
        "   -h, --help            print this message and exit\n"
        "   -i, --input <file>    input file to decode\n"
        "   -o, --output <file>   output YUV file to write to (output.yuv by default)\n"
        "   --dump-info <file>    dump stream info to <file>\n"
        "   --monochrome          dump in grayscale format\n"
        "   --no-simd             disable all SIMD functions\n"
        "   --no-sse              disable SSE SIMD functions\n"
        "   --no-avx2             disable AVX2 SIMD functions\n"
        "   --no-output           don't write to an output file (use when benchmarking)\n"
        "   --no-deblock          disable in-loop deblocking filter\n"
    );
}


#define CLI_ERR(message) \
    fprintf(stderr, message); \
    return 1;

static int parse_options(int argc, char *argv[], Undo264Context *ctx) {
    if (argc == 1) {
        print_usage();
        exit(1);
    }

    bool input_file_specified = false;
    for (int i = 1; i < argc; i++) {
        const char *arg = argv[i];

        if (!strcmp(arg, "--help") || !strcmp(arg, "-h")) {
            print_usage();
            exit(0);
        } else if (!strcmp(arg, "--input") || !strcmp(arg, "-i")) {
            if (++i >= argc) { CLI_ERR("--input needs a value\n") }
            ctx->in_path = argv[i];
            input_file_specified = true;
        } else if (!strcmp(arg, "--output") || !strcmp(arg, "-o")) {
            if (++i >= argc) { CLI_ERR("--output needs a value\n") }
            ctx->out_path = argv[i];
        } else if (!strcmp(arg, "--monochrome")) {
            ctx->cli_options->dump_monochrome = true;
        } else if (!strcmp(arg, "--no-simd")) {
            ctx->cli_options->use_simd = false;
        } else if (!strcmp(arg, "--no-sse")) {
            ctx->cli_options->use_sse = false;
        } else if (!strcmp(arg, "--no-avx2")) {
            ctx->cli_options->use_avx2 = false;
        } else if (!strcmp(arg, "--no-output")) {
            ctx->cli_options->dump_frames = false;
        } else if (!strcmp(arg, "--no-deblock")) {
            ctx->cli_options->deblock = false;
        } else if (!strcmp(arg, "--dump-info")) {
            if (++i >= argc) { CLI_ERR("--dump-info needs a value\n") }
            ctx->info_out_path = argv[i];
            ctx->cli_options->dump_info = true;
        } else {
            fprintf(stderr, "unrecognized option : %s\n", arg);
            print_usage();
            return 1;
        }
    }

    if (!input_file_specified) {
        CLI_ERR("please specify an input file.\n")
    }

    return 0;
}


static int open_files(Undo264Context *ctx) {
    FILE *test_input = fopen(ctx->in_path, "rb");
    if (!test_input) {
        fprintf(stderr, "input file not found: %s\n", ctx->in_path);
        return 1;
    }
    fclose(test_input);


    /* input file, uses memory mapping which is platform-specific */
#ifdef UNIX
    int fd = open(ctx->in_path, O_RDONLY);
    if (fd < 0) {
        perror("open");
        return 1;
    }
    struct stat st;
    fstat(fd, &st);
    if (st.st_size == 0) {
        fprintf(stderr, "Opening input file : file is empty\n");
        close(fd);
        return 1;
    }
    ctx->data_size = st.st_size;

    ctx->data = mmap(NULL, ctx->data_size, PROT_READ, MAP_PRIVATE, fd, 0);
    close(fd);
    if (ctx->data == MAP_FAILED) {
        perror("mmap");
        return 1;
    }
    madvise(ctx->data, ctx->data_size, MADV_SEQUENTIAL);
    ctx->data_is_mapped = true;
#endif

#ifdef WIN32
    TCHAR *lpFileName = TEXT(ctx->in_path);
    HANDLE hFile;
    HANDLE hMap;
    LPVOID lpBasePtr;
    LARGE_INTEGER liFileSize;

    hFile = CreateFile(lpFileName, GENERIC_READ, 0, NULL, OPEN_EXISTING, FILE_ATTRIBUTE    /* opening input and output files */_NORMAL, 0);
    if (hFile == INVALID_HANDLE_VALUE) {
        fprintf(stderr, "Opening input file : CreateFile failed with error %d\n", GetLastError());
        exit(1);
    }
    if (!GetFileSizeEx(hFile, &liFileSize)) {
        fprintf(stderr, "Opening input file : GetFileSizeEx failed with error %d\n", GetLastError());
        CloseHandle(hFile);
        exit(1);
    }
    if (liFileSize.QuadPart == 0) {
        fprintf(stderr, "Opening input file : file is empty\n");
        CloseHandle(hFile);
        exit(1);
    }

    hMap = CreateFileMapping(hFile, NULL, PAGE_READONLY, 0, 0, NULL);
    if (hMap == 0) {
        fprintf(stderr, "Opening input file : CreateFileMapping failed with error %d\n", GetLastError());
        CloseHandle(hFile);
        exit(1);
    }

    lpBasePtr = MapViewOfFile(hMap, FILE_MAP_READ, 0, 0, 0);
    if (lpBasePtr == NULL) {
        fprintf(stderr, "Opening input file : MapViewOfFile failed with error %d\n", GetLastError());
        CloseHandle(hFile);
        CloseHandle(hMap);
        exit(1);
    }
    ctx->data = lpBasePtr;
    ctx->data_size = (size_t)liFileSize.QuadPart;
    ctx->lpBasePtr = lpBasePtr;
    ctx->hMap = hMap;
    ctx->hFile = hFile;
#endif


    /* output .yuv file */
    if (ctx->cli_options->dump_frames) {
        ctx->out_file = fopen(ctx->out_path, "wb");
        if (!ctx->out_file) {
            perror("fopen");
            return 1;
        }
        setvbuf(ctx->out_file, NULL, _IOFBF, (size_t)(8 * 1920*1080*1.5)); // 8 frame buffer for HD
    }

    /* info .json file for viewer */
    if (ctx->cli_options->dump_info) {
        ctx->info_out_file = fopen(ctx->info_out_path, "wb");
        if (!ctx->info_out_file) {
            perror("fopen");
            return 1;
        }
    }

    return 0;
}


int main(int argc, char *argv[]) {


    Undo264Context *undo264ctx = undo264_context_make();
    if (!undo264ctx) {
        perror("calloc");
        return 1;
    }

    if (parse_options(argc, argv, undo264ctx) != 0) {
        return 1;
    }


    int ret = open_files(undo264ctx);
    if (ret != 0) return ret;


    decoder_init(undo264ctx);

    undo_status st = decoder_run(undo264ctx);
    if (st != UNDO_OK) {
        fprintf(stderr, "undo264: %s\n", decoder_error_msg(undo264ctx));
        decoder_free(undo264ctx);
        return 1 + (int)st;
    }



    long long total_frames_us = get_total_frame_microseconds(undo264ctx->prf);

    size_t total_frames = undo264ctx->prf->total_frames;
    if (total_frames > 0) {
        printf("Done, decoded %lu frames, avg : %.3fms per frame (%.3ffps)\n",
            undo264ctx->prf->total_frames,
            (double)total_frames_us / (double)total_frames / 1000,
            (double)((double)total_frames / ((double)total_frames_us/1000000))
        );
    }


    decoder_free(undo264ctx);


    return 0;
}