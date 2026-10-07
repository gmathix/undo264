//
// Created by gmathix on 5/12/26.
//



#include "loopfilter.h"


#include "dpb.h"
#include "dsp_init.h"
#include "mb.h"


#include "util/mbutil.h"


const uint8_t alpha_table[52] = {
      0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0,
      0,   0,   0,   4,   4,   5,   6,   7,   8,   9,  10,  12,  13,
     15,  17,  20,  22,  25,  28,  32,  36,  40,  45,  50,  56,  63,
     71,  80,  90, 101, 113, 127, 144, 162, 182, 203, 226, 255, 255,
};

const uint8_t beta_table[52] = {
     0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,
     0,  0,  0,  2,  2,  2,  3,  3,  3,  3,  4,  4,  4,
     6,  6,  7,  7,  8,  8,  9,  9, 10, 10, 11, 11, 12,
    12, 13, 13, 14, 14, 15, 15, 16, 16, 17, 17, 18, 18,
};

const uint8_t treshold_table[3][52] = {
    {  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,
       0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  1,  1,  1,
       1,  1,  1,  1,  1,  1,  1,  2,  2,  2,  2,  3,  3,
       3,  4,  4,  4,  5,  6,  6,  7,  8,  9, 10, 11, 13,
    },

    {  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,
       0,  0,  0,  0,  0,  0,  0,  0,  1,  1,  1,  1,  1,
       1,  1,  1,  1,  1,  2,  2,  2,  2,  3,  3,  3,  4,
       4,  5,  5,  6,  7,  8,  8, 10, 11, 12, 13, 15, 17,
    },

    {  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,
       0,  0,  0,  0,  1,  1,  1,  1,  1,  1,  1,  1,  1,
       1,  2,  2,  2,  2,  3,  3,  3,  4,  4,  4,  5,  6,
       6,  7,  8,  9, 10, 11, 13, 14, 16, 18, 20, 23, 25,
    },
};



int tc0_tables_initialized = 0;
int8_t tc0_tables[256][52][16] __attribute__((aligned(16)));



// this is ugly but for now it's not slow enough to require refactoring
static void fill_ctx_cache(bool filter_left, bool filter_top, int mb_width, Macroblock *mb, Picture *pic, Undo264Context *ctx) {
    if (filter_left) {
        int mbAddrA = mb->mbAddr - 1;
        MacroblockMetadata metaA = ctx->mb_metadata[mbAddrA];
        for (int i = 0; i < 4; i++) {
            int i8x8 = 1 + (i/2)*2;
            ctx->ref_cache[L0][i]       = pic->ref_pics[mbAddrA][L0][3+(i<<2)]->dpb_pic_id;
            ctx->ref_cache[L1][i]       = pic->ref_pics[mbAddrA][L1][3+(i<<2)]->dpb_pic_id;
            ctx->mv_cache[L0][i<<1]     = pic->motion_val[mbAddrA][L0][3+(i<<2)][0];
            ctx->mv_cache[L0][(i<<1)+1] = pic->motion_val[mbAddrA][L0][3+(i<<2)][1];
            ctx->mv_cache[L1][i<<1]     = pic->motion_val[mbAddrA][L1][3+(i<<2)][0];
            ctx->mv_cache[L1][(i<<1)+1] = pic->motion_val[mbAddrA][L1][3+(i<<2)][1];
            if (!metaA.t_8x8_flag) ctx->total_coeff_cache[i]   = ctx->total_coeffs[mbAddrA][3+(i<<2)];
            else ctx->total_coeff_cache[i] = (metaA.cbp_luma & (1 << i8x8)) > 0;
        }
    }
    MacroblockMetadata meta = ctx->mb_metadata[mb->mbAddr];
    for (int i = 0 ; i < 16; i++) {
        ctx->ref_cache[L0][4+i] = pic->ref_pics[mb->mbAddr][L0][i]->dpb_pic_id;
        ctx->ref_cache[L1][4+i] = pic->ref_pics[mb->mbAddr][L1][i]->dpb_pic_id;
    }
    memcpy(&ctx->mv_cache[L0][8], &pic->motion_val[mb->mbAddr][L0][0], 32 * sizeof(int16_t));
    memcpy(&ctx->mv_cache[L1][8], &pic->motion_val[mb->mbAddr][L1][0], 32 * sizeof(int16_t));
    if (!meta.t_8x8_flag) memcpy(&ctx->total_coeff_cache[4], &ctx->total_coeffs[mb->mbAddr][0], 16 * sizeof(uint8_t));
    else {
        for (int i8x8 = 0; i8x8 < 4; i8x8++) {
            int tc = (meta.cbp_luma & (1 << i8x8)) > 0;
            int base = map_4x4[i8x8<<2];
            ctx->total_coeff_cache[4+base+0] = tc;
            ctx->total_coeff_cache[4+base+1] = tc;
            ctx->total_coeff_cache[4+base+4] = tc;
            ctx->total_coeff_cache[4+base+5] = tc;
        }
    }

    if (filter_top) {
        int mbAddrB = mb->mbAddr - mb_width;
        MacroblockMetadata metaB = ctx->mb_metadata[mbAddrB];
        for (int i = 0; i < 4; i++) {
            ctx->ref_cache[L0][20+i]       = pic->ref_pics[mbAddrB][L0][12+i]->dpb_pic_id;
            ctx->ref_cache[L1][20+i]       = pic->ref_pics[mbAddrB][L1][12+i]->dpb_pic_id;
        }
        memcpy(&ctx->mv_cache[L0][40], &pic->motion_val[mbAddrB][L0][12], 8 * sizeof(int16_t));
        memcpy(&ctx->mv_cache[L1][40], &pic->motion_val[mbAddrB][L1][12], 8 * sizeof(int16_t));
        if (!metaB.t_8x8_flag) memcpy(&ctx->total_coeff_cache[20], &ctx->total_coeffs[mbAddrB][12], 4 * sizeof(uint8_t));
        else {
            for (int i8x8 = 2; i8x8 < 4; i8x8++) {
                int tc = (metaB.cbp_luma & (1 << i8x8)) > 0;
                ctx->total_coeff_cache[20+i8x8*2-4] = tc;
                ctx->total_coeff_cache[20+i8x8*2-3] = tc;
            }
        }
    }
}


// inspired from how ffmpeg does it
static int check_mv(SliceHeader *sh, int idx, int idx_n, const Undo264Context *ctx) {
    int bs;

    int idx_mv = idx << 1;
    int idx_n_mv = idx_n << 1;

    // L0
    bs = ctx->ref_cache[L0][idx] != ctx->ref_cache[L0][idx_n]; // different pictures
    if (bs == 0 && ctx->ref_cache[L0][idx] != EMPTY_PICTURE.dpb_pic_id) {
        bs = ctx->mv_cache[L0][idx_mv]   - ctx->mv_cache[L0][idx_n_mv]   + 3 >= 7U | // abs(MV_x - MV_x_n) >= 4
             ctx->mv_cache[L0][idx_mv+1] - ctx->mv_cache[L0][idx_n_mv+1] + 3 >= 7U;  // abs(MV_y - MV_y_n) >= 4
    }

    if (IS_B_SLICE(sh->slice_type)) {
        if (bs == 0) { // L1
            bs = ctx->ref_cache[L1][idx] != ctx->ref_cache[L1][idx_n] | // different L1 pictures
                 ctx->mv_cache[L1][idx_mv]   - ctx->mv_cache[L1][idx_n_mv]   + 3 >= 7U | // abs(MV_x - MV_x_n) >= 4
                 ctx->mv_cache[L1][idx_mv+1] - ctx->mv_cache[L1][idx_n_mv+1] + 3 >= 7U;  // abs(MV_y - MV_y_n) >= 4
        }

        if (bs == 1) { // L0 pictures are different and L1 pictures too, need to check if L0 and L1 pictures are mutually different too
            if (ctx->ref_cache[L0][idx] != ctx->ref_cache[L1][idx_n] |
                ctx->ref_cache[L1][idx] != ctx->ref_cache[L0][idx_n])
                return 1;

            return ctx->mv_cache[L0][idx_mv]   - ctx->mv_cache[L1][idx_n_mv]   + 3 >= 7U | // abs(MV_x_l0 - MV_x_n_l1) >= 4
                   ctx->mv_cache[L0][idx_mv+1] - ctx->mv_cache[L1][idx_n_mv+1] + 3 >= 7U | // abs(MV_y_l0 - MV_x_y_l1) >= 4
                   ctx->mv_cache[L1][idx_mv]   - ctx->mv_cache[L0][idx_n_mv]   + 3 >= 7U | // abs(MV_x_l1 - MV_x_n_l0) >= 4
                   ctx->mv_cache[L1][idx_mv+1] - ctx->mv_cache[L0][idx_n_mv+1] + 3 >= 7U;  // abs(MV_y_l1 - MV_y_n_l0) >= 4
        }
    }

    return bs;
}
// derive bS for all edges where not intra
static void derive_low_bS_list_fast(SliceHeader *sh, int bS_list[2][4][4], const Undo264Context* ctx) {
    // vertical
    for (int edge = 0; edge < 4; edge++) {
        for (int i = 0; i < 4; i++) {
            int idx_n = edge == 0 ? i : 4+(i<<2)+edge-1;
            int idx = ((i+1)<<2)+edge;
            if (ctx->total_coeff_cache[idx_n] || ctx->total_coeff_cache[idx]) {
                bS_list[0][edge][i] = 2;
            } else {
                bS_list[0][edge][i] = check_mv(sh, idx, idx_n, ctx);
            }
        }
    }

    // horizontal
    for (int edge = 0; edge < 4; edge++) {
        for (int i = 0; i < 4; i++) {
            int top_pos = edge == 0 ? 20 : edge<<2;
            if (ctx->total_coeff_cache[top_pos+i] || ctx->total_coeff_cache[4+(edge<<2)+i]) {
                bS_list[1][edge][i] = 2;
            } else {
                bS_list[1][edge][i] = check_mv(sh, 4+(edge<<2)+i, top_pos+i, ctx);
            }
        }
    }
}


static void derive_alpha_beta(Picture *pic, int mbAddr, int mbAddrN, uint8_t alpha[3], uint8_t beta[3], uint8_t indexA[3], const Undo264Context *ctx) {
    MacroblockMetadata *meta0 = &ctx->mb_metadata[mbAddr];
    MacroblockMetadata *meta1 = &ctx->mb_metadata[mbAddrN];

    // Y plane
    int qp0 = !IS_PCM(meta0->mb_type) * meta0->QPY;
    int qp1 = !IS_PCM(meta1->mb_type) * meta1->QPY;
    int qpAv = (qp0 + qp1 + 1) >> 1;

    indexA[0]  = _clip3(0, 51, qpAv + pic->sh->slice_alpha_c0_offset_div2 * 2);
    int indexB = _clip3(0, 51, qpAv + pic->sh->slice_beta_offset_div2 * 2);

    alpha[0] = alpha_table[indexA[0]]; // assume bitDepth = 8
    beta[0]  = beta_table[indexB];

    // Cb / Cr planes
    for (int i = 0; i < 2; i++) {
        qp0 = !IS_PCM(meta0->mb_type) * meta0->QPC[i];
        qp1 = !IS_PCM(meta1->mb_type) * meta1->QPC[i];
        qpAv = (qp0 + qp1 + 1) >> 1;

        indexA[1+i] = _clip3(0, 51, qpAv + pic->sh->slice_alpha_c0_offset_div2 * 2);
        indexB      = _clip3(0, 51, qpAv + pic->sh->slice_beta_offset_div2 * 2);

        alpha[1+i] = alpha_table[indexA[1+i]];
        beta[1+i]  = beta_table[indexB];
    }
}


static void deblock_macroblock(Picture *pic, SliceHeader *sh, int mbAddr, const Undo264Context *ctx) {

    SPS *sps = sh->sps;


    // make dummy mb just for accessing the neighbors afterward
    Macroblock *mb = ctx->scratchMb;
    reset_mb(mb, mbAddr, ctx);
    derive_macroblock_neighbors(mb, sh->first_mb, ctx);


    const int widthY     = pic->widthY;
    const int widthC     = pic->widthC;
    const int mbWidth    = sps->pic_width_in_mbs;

    const bool disableSliceBoundaries = sh->disable_deblocking_filter_idc == 2;
    const bool filterInternalEdges    = sh->disable_deblocking_filter_idc != 1;
    const bool filterLeftMbEdge       = filterInternalEdges && (mb->mbAddr % mbWidth != 0) && (!disableSliceBoundaries || mb->has_mb_a);
    const bool filterTopMbEdge        = filterInternalEdges && (mb->mbAddr >= mbWidth) && (!disableSliceBoundaries || mb->has_mb_b);


    fill_ctx_cache(filterLeftMbEdge, filterTopMbEdge, mbWidth, mb, ctx->curr_pic, ctx);


    const int luma_pos   = mb->mb_y*16*widthY + mb->mb_x*16;
    const int chroma_pos = mb->mb_y*8*widthC + mb->mb_x*8;
    uint8_t *luma_base_dst = &pic->luma[luma_pos];
    uint8_t *cb_base_dst   = &pic->cb[chroma_pos];
    uint8_t *cr_base_dst   = &pic->cr[chroma_pos];

    int bS_list[4] = {0, 0, 0, 0};
    int bS_list_all[2][4][4] = {};

    uint8_t alphaLeft[3], betaLeft[3], indexALeft[3];
    uint8_t alphaTop[3],  betaTop[3],  indexATop[3];
    uint8_t alphaIn[3],   betaIn[3],   indexAIn[3];

    bool mb8x8 = ctx->mb_metadata[mb->mbAddr].t_8x8_flag;

    derive_low_bS_list_fast(sh, bS_list_all, ctx);


    if (filterLeftMbEdge) {
        // x = 0
        derive_alpha_beta(pic, mbAddr, mbAddr - 1, alphaLeft, betaLeft, indexALeft, ctx);
        if (IS_INTRA(ctx->mb_metadata[mbAddr-1].mb_type)) {
            ctx->dsp->deblock_edge_strong_luma_v(luma_base_dst, widthY, alphaLeft[0], betaLeft[0]);
            ctx->dsp->deblock_edge_strong_chroma_v(cb_base_dst, widthC, alphaLeft[1], betaLeft[1]);
            ctx->dsp->deblock_edge_strong_chroma_v(cr_base_dst, widthC, alphaLeft[2], betaLeft[2]);
        } else {
            ctx->dsp->deblock_edge_weak_luma_v(luma_base_dst, widthY, alphaLeft[0], betaLeft[0], indexALeft[0], bS_list_all[0][0]);
            ctx->dsp->deblock_edge_weak_chroma_v(cb_base_dst, widthC, alphaLeft[1], betaLeft[1], indexALeft[1], bS_list_all[0][0]);
            ctx->dsp->deblock_edge_weak_chroma_v(cr_base_dst, widthC, alphaLeft[2], betaLeft[2], indexALeft[2], bS_list_all[0][0]);
        }
    }
    if (filterInternalEdges) {
        derive_alpha_beta(pic, mbAddr, mbAddr, alphaIn, betaIn, indexAIn, ctx);

        // x = 4
        if (!mb8x8) {
            ctx->dsp->deblock_edge_weak_luma_v(luma_base_dst + 4, widthY, alphaIn[0], betaIn[0], indexAIn[0], bS_list_all[0][1]);
        }

        // x = 8
        ctx->dsp->deblock_edge_weak_luma_v(luma_base_dst + 8, widthY, alphaIn[0], betaIn[0], indexAIn[0], bS_list_all[0][2]);
        ctx->dsp->deblock_edge_weak_chroma_v(cb_base_dst + 4, widthC, alphaIn[1], betaIn[1], indexAIn[1], bS_list_all[0][2]);
        ctx->dsp->deblock_edge_weak_chroma_v(cr_base_dst + 4, widthC, alphaIn[2], betaIn[2], indexAIn[2], bS_list_all[0][2]);

        // x = 12
        if (!mb8x8) {
            ctx->dsp->deblock_edge_weak_luma_v(luma_base_dst + 12, widthY, alphaIn[0], betaIn[0], indexAIn[0], bS_list_all[0][3]);
        }
    }


    if (filterTopMbEdge) {
        // y = 0
        derive_alpha_beta(pic, mbAddr, mbAddr - mbWidth, alphaTop, betaTop, indexATop, ctx);

        if (IS_INTRA(ctx->mb_metadata[mbAddr - mbWidth].mb_type)) {
            ctx->dsp->deblock_edge_strong_luma_h(luma_base_dst, widthY, alphaTop[0], betaTop[0]);
            ctx->dsp->deblock_edge_strong_chroma_h(cb_base_dst, widthC, alphaTop[1], betaTop[1]);
            ctx->dsp->deblock_edge_strong_chroma_h(cr_base_dst, widthC, alphaTop[2], betaTop[2]);
        } else {
            ctx->dsp->deblock_edge_weak_luma_h(luma_base_dst, widthY, alphaTop[0], betaTop[0], indexATop[0], bS_list_all[1][0]);
            ctx->dsp->deblock_edge_weak_chroma_h(cb_base_dst, widthC, alphaTop[1], betaTop[1], indexATop[1], bS_list_all[1][0]);
            ctx->dsp->deblock_edge_weak_chroma_h(cr_base_dst, widthC, alphaTop[2], betaTop[2], indexATop[2], bS_list_all[1][0]);
        }
    }
    if (filterInternalEdges) {
        // y = 4
        if (!mb8x8) {
            ctx->dsp->deblock_edge_weak_luma_h(luma_base_dst + 4*widthY, widthY, alphaIn[0], betaIn[0], indexAIn[0], bS_list_all[1][1]);
        }

        // y = 8
        ctx->dsp->deblock_edge_weak_luma_h(luma_base_dst + 8*widthY, widthY, alphaIn[0], betaIn[0], indexAIn[0], bS_list_all[1][2]);
        ctx->dsp->deblock_edge_weak_chroma_h(cb_base_dst + 4*widthC, widthC, alphaIn[1], betaIn[1], indexAIn[1], bS_list_all[1][2]);
        ctx->dsp->deblock_edge_weak_chroma_h(cr_base_dst + 4*widthC, widthC, alphaIn[2], betaIn[2], indexAIn[2], bS_list_all[1][2]);

        // y = 12
        if (!mb8x8) {
            ctx->dsp->deblock_edge_weak_luma_h(luma_base_dst + 12*widthY, widthY, alphaIn[0], betaIn[0], indexAIn[0], bS_list_all[1][3]);
        }
    }
}

static void deblock_macroblock_intra(Picture *pic, SliceHeader *sh, int mbAddr, const Undo264Context *ctx) {
    SPS *sps = sh->sps;

    // make dummy mb just for accessing the neighbors afterward
    Macroblock *mb = ctx->scratchMb;
    reset_mb(mb, mbAddr, ctx);
    derive_macroblock_neighbors(mb, sh->first_mb, ctx);


    const int widthY     = pic->widthY;
    const int widthC     = pic->widthC;
    const int mbWidth    = sps->pic_width_in_mbs;

    const bool disableSliceBoundaries = sh->disable_deblocking_filter_idc == 2;
    const bool filterInternalEdges    = sh->disable_deblocking_filter_idc != 1;
    const bool filterLeftMbEdge       = filterInternalEdges && (mb->mbAddr % mbWidth != 0) && (!disableSliceBoundaries || mb->has_mb_a);
    const bool filterTopMbEdge        = filterInternalEdges && (mb->mbAddr >= mbWidth) && (!disableSliceBoundaries || mb->has_mb_b);


    const int luma_pos   = mb->mb_y*16*widthY + mb->mb_x*16;
    const int chroma_pos = mb->mb_y*8*widthC + mb->mb_x*8;
    uint8_t *luma_base_dst = &pic->luma[luma_pos];
    uint8_t *cb_base_dst   = &pic->cb[chroma_pos];
    uint8_t *cr_base_dst   = &pic->cr[chroma_pos];

    const int bS_list[4] = {3, 3, 3, 3};

    uint8_t alphaLeft[3], betaLeft[3], indexALeft[3];
    uint8_t alphaTop[3],  betaTop[3],  indexATop[3];
    uint8_t alphaIn[3],   betaIn[3],   indexAIn[3];

    bool mb8x8 = ctx->mb_metadata[mbAddr].t_8x8_flag;


    if (filterLeftMbEdge) {
        // x = 0
        derive_alpha_beta(pic, mbAddr, mbAddr - 1, alphaLeft, betaLeft, indexALeft, ctx);

        ctx->dsp->deblock_edge_strong_luma_v(luma_base_dst, widthY, alphaLeft[0], betaLeft[0]);
        ctx->dsp->deblock_edge_strong_chroma_v(cb_base_dst, widthC, alphaLeft[1], betaLeft[1]);
        ctx->dsp->deblock_edge_strong_chroma_v(cr_base_dst, widthC, alphaLeft[2], betaLeft[2]);
    }
    if (filterInternalEdges) {
        derive_alpha_beta(pic, mbAddr, mbAddr, alphaIn, betaIn, indexAIn, ctx);

        // x = 4
        if (!mb8x8) {
            ctx->dsp->deblock_edge_weak_luma_v(luma_base_dst + 4, widthY, alphaIn[0], betaIn[0], indexAIn[0], bS_list);
        }

        // x = 8
        ctx->dsp->deblock_edge_weak_luma_v(luma_base_dst + 8, widthY, alphaIn[0], betaIn[0], indexAIn[0], bS_list);
        ctx->dsp->deblock_edge_weak_chroma_v(cb_base_dst + 4, widthC, alphaIn[1], betaIn[1], indexAIn[1], bS_list);
        ctx->dsp->deblock_edge_weak_chroma_v(cr_base_dst + 4, widthC, alphaIn[2], betaIn[2], indexAIn[2], bS_list);

        // x = 12
        if (!mb8x8) {
            ctx->dsp->deblock_edge_weak_luma_v(luma_base_dst + 12, widthY, alphaIn[0], betaIn[0], indexAIn[0], bS_list);
        }
    }


    if (filterTopMbEdge) {
        // y = 0
        derive_alpha_beta(pic, mbAddr, mbAddr - mbWidth, alphaTop, betaTop, indexATop, ctx);

        ctx->dsp->deblock_edge_strong_luma_h(luma_base_dst, widthY, alphaTop[0], betaTop[0]);
        ctx->dsp->deblock_edge_strong_chroma_h(cb_base_dst, widthC, alphaTop[1], betaTop[1]);
        ctx->dsp->deblock_edge_strong_chroma_h(cr_base_dst, widthC, alphaTop[2], betaTop[2]);
    }
    if (filterInternalEdges) {
        // y = 4
        if (!mb8x8) {
            ctx->dsp->deblock_edge_weak_luma_h(luma_base_dst + 4*widthY, widthY, alphaIn[0], betaIn[0], indexAIn[0], bS_list);
        }

        // y = 8
        ctx->dsp->deblock_edge_weak_luma_h(luma_base_dst + 8*widthY, widthY, alphaIn[0], betaIn[0], indexAIn[0], bS_list);
        ctx->dsp->deblock_edge_weak_chroma_h(cb_base_dst + 4*widthC, widthC, alphaIn[1], betaIn[1], indexAIn[1], bS_list);
        ctx->dsp->deblock_edge_weak_chroma_h(cr_base_dst + 4*widthC, widthC, alphaIn[2], betaIn[2], indexAIn[2], bS_list);

        // y = 12
        if (!mb8x8) {
            ctx->dsp->deblock_edge_weak_luma_h(luma_base_dst + 12*widthY, widthY, alphaIn[0], betaIn[0], indexAIn[0], bS_list);
        }
    }
}




void init_tc0_tables(void) {
    for (int bs0 = 0; bs0 < 4; bs0++) {
        for (int bs1 = 0; bs1 < 4; bs1++) {
            for (int bs2 = 0; bs2 < 4; bs2++) {
                for (int bs3 = 0; bs3 < 4; bs3++) {
                    for (int indexA = 0; indexA < 52; indexA++) {
                        int8_t *table = &tc0_tables[(bs0<<6) + (bs1<<4) + (bs2<<2) + bs3][indexA][0];
                        memset(table +  0, bs0 ? treshold_table[bs0-1][indexA] : (int8_t)-1, 4);
                        memset(table +  4, bs1 ? treshold_table[bs1-1][indexA] : (int8_t)-1, 4);
                        memset(table +  8, bs2 ? treshold_table[bs2-1][indexA] : (int8_t)-1, 4);
                        memset(table + 12, bs3 ? treshold_table[bs3-1][indexA] : (int8_t)-1, 4);
                    }
                }
            }
        }
    }
}

void deblock_slice(Picture *pic, SliceHeader *sh, const Undo264Context *ctx) {
    if (!tc0_tables_initialized) {
        init_tc0_tables();
        tc0_tables_initialized = 1;
    }
    for (unsigned i = sh->first_mb; i < sh->first_mb + ctx->current_slice->num_mbs; i++) {
        if (IS_INTRA(ctx->mb_metadata[i].mb_type)) {
            deblock_macroblock_intra(pic, sh, i, ctx);
        } else {
            deblock_macroblock(pic, sh, i, ctx);
        }
    }
}