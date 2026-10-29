//
// Created by gmathix on 10/21/26.
//


#include <stddef.h>
#include <stdint.h>

#include "decoder.h"

extern int LLVMFuzzerTestOneInput(const uint8_t *Data, size_t Size) {
    Undo264Context *ctx = undo264_context_make();
    decoder_init(ctx);
    ctx->data = Data;
    ctx->data_size = Size;

    ctx->cli_options->dump_frames = false;

    undo_status st = decoder_run(ctx);

    decoder_free(ctx);

    return 0;  // Non-zero return values are reserved for future use.
}
