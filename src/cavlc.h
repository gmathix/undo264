//
// Created by gmathix on 3/30/26.
//

#ifndef H264_CAVLC_H
#define H264_CAVLC_H



#include "global.h"
#include "mb.h"
#include "slice.h"


#define MAX_CODE_LENGTH   16





#define MAX_COEFF_TOKEN_BITS    16
#define MAX_TOTAL_ZEROS_BITS     9
#define MAX_RUN_BEFORE_BITS     11


extern const uint16_t coeff_token_lengths      [4][62];
extern const uint16_t coeff_token_bits         [4][62];

extern const uint16_t coeff_token_cr420_lengths   [14];
extern const uint16_t coeff_token_cr420_bits      [14];

extern const uint16_t coeff_token_cr422_lengths   [30];
extern const uint16_t coeff_token_cr422_bits      [30];

extern const uint16_t total_zeros_lengths     [15][16];
extern const uint16_t total_zeros_bits        [15][16];

extern const uint16_t total_zeros_cr420_lengths [3][4];
extern const uint16_t total_zeros_cr420_bits    [3][4];
extern const uint16_t total_zeros_cr422_lengths [7][8];
extern const uint16_t total_zeros_cr422_bits    [7][8];

extern const uint16_t run_before_lengths        [7][15];
extern const uint16_t run_before_bits           [7][15];





void  residual_block_cavlc (Macroblock *mb, int blkIdx, int iCbCr, int bt,
    int16_t *coeffLevel, int startIdx, int endIdx, int maxNumCoeff, bool isLuma,
    SliceHeader *sh, Undo264Context *ctx);


void  coeff_token    (Macroblock *mb, int blkIdx, int iCbCr, BlockType blockType, bool isLuma,
                        int *totalCoeff, int *trailingOnes, int *nC,
                        SliceHeader *sh, const Undo264Context *ctx);
void  parse_level    (int16_t levelVal[], int totalCoeff, int trailingOnes, const Undo264Context *ctx);
void  parse_run      (int16_t runVal[], int totalCoeff, int maxNumCoeff, int startIdx, int endIdx, const Undo264Context *ctx);
void  reconstruct    (const int16_t levelVal[], const int16_t runVal[], int16_t coeffLevel[], int startIdx, int totalCoeff);

#endif //H264_CAVLC_H