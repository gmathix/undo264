//
// Created by gmathix on 3/18/26.
//


#include <stdio.h>
#include <stdlib.h>

#include "decoder.h"

#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>

#include "tests/profiler.h"

#include "sys/mman.h"





static void print_usage(void) {
    fprintf(stderr,
        "Usage: undo264 -i <input> [OPTIONS]\n\n"
        "Options:\n"
        "   -h, --help            print this message and exit\n"
        "   -i, --input           input file to decode\n"
        "   -o, --output          output YUV file to write to (output.yuv by default)\n"
        "   --monochrome          dump in grayscale format\n"
        "   --no-simd             disable all SIMD functions\n"
        "   --no-sse              disable SSE SIMD functions\n"
        "   --no-avx2             disable AVX2 SIMD functions\n"
        "   --no-output           don't write to an output file (use when benchmarking)\n"
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
        } else {
            fprintf(stderr, "unrecognized option : %s\n", arg);
            return 1;
        }
    }

    if (!input_file_specified) {
        CLI_ERR("please specify an input file.\n")
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



    decoder_init(undo264ctx);

    decoder_run(undo264ctx);




    long long total_frames_us = get_total_frame_microseconds(undo264ctx->prf);

    size_t total_frames = undo264ctx->prf->total_frames;

    printf("Done, decoded %lu frames, avg : %.3fms per frame (%.3ffps)\n",
        undo264ctx->prf->total_frames,
        (double)total_frames_us / (double)total_frames / 1000,
        (double)((double)total_frames / ((double)total_frames_us/1000000))
    );


    decoder_free(undo264ctx);


    return 0;
}
