//
// Created by gmathix on 4/7/26.
//
#include "picture.h"

#include "error.h"
#include "slice.h"
#include "util/dump_info.h"


Picture EMPTY_PICTURE = {};


Picture *picture_alloc(SPS *sps, const Undo264Context *ctx) {
    Picture *p  = calloc_check(1, sizeof(Picture), ctx);

	p->widthY      = sps->pic_width_samples_l;
	p->heightY     = sps->pic_height_samples_l;
	p->widthC      = p->widthY / 2;
	p->heightC     = p->heightY / 2;
	p->widthCropY  = p->widthY - sps->crop_right_offset - sps->crop_left_offset;
	p->heightCropY = p->heightY - sps->crop_bottom_offset - sps->crop_top_offset;
	p->widthCropC  = p->widthCropY / 2;
	p->heightCropC = p->heightCropY / 2;
    p->num_mbs     = (int32_t)sps->pic_width_in_mbs * (int32_t)sps->pic_height_in_map_units;

    p->luma        = calloc_check(p->widthY * p->heightY, 1, ctx);

    p->cb          = calloc_check(p->widthY/2 * (p->heightY/2), 1, ctx);
    p->cr          = calloc_check(p->widthY/2 * (p->heightY/2), 1, ctx);

    p->mb_types     = calloc_check(p->num_mbs, sizeof( int ), ctx);
    p->pred_flags   = calloc_check(p->num_mbs, sizeof( bool [2][4] ), ctx);

    p->motion_val   = calloc_check(p->num_mbs, sizeof(int16_t  [2][16][2]), ctx);
    p->ref_idx      = calloc_check(p->num_mbs, sizeof(int8_t   [2][16]), ctx);
    p->ref_pics     = calloc_check(p->num_mbs, sizeof(Picture* [2][16]), ctx);

    p->json_payload = make_picture_json_payload(ctx);

    return p;
}



void picture_reset(Picture *p, Undo264Context *ctx) {
    p->frame_num = 0;
    p->frame_num_offset = 0;
    p->frame_num_wrap = 0;
    p->pic_num = 0;
    p->poc = 0;
    p->dpb_pic_id = 0;
    memset(p->in_list, false, 2);
    memset(p->lowest_list_index, 0, 2);

    p->dpb_status = UNUSED_REF;
    p->long_term_frame_idx = 0;
    p->is_output = false;
    p->top_field_order_cnt = 0;
    p->bottom_field_order_cnt = 0;

    free_picture_json_payload(p->json_payload);
    p->json_payload = make_picture_json_payload(ctx);
}

void picture_free(Picture *p) {
    free(p->luma);
    free(p->cb);
    free(p->cr);

    free(p->mb_types);
    free(p->pred_flags);
    free(p->motion_val);
    free(p->ref_idx);
    free(p->ref_pics);

    free_picture_json_payload(p->json_payload);

    free(p);
}

void dump_picture(Picture *p, const Undo264Context *ctx) {
    if (!ctx->cli_options->dump_frames) return;

    int top    = ctx->ps->sps->crop_top_offset;
    int bottom = ctx->ps->sps->crop_bottom_offset;
    int left   = ctx->ps->sps->crop_left_offset;
    int right  = ctx->ps->sps->crop_right_offset;


    for (int i = top; i < p->heightY - bottom; i++) {
        fwrite(&p->luma[i*p->widthY + left],   1, p->widthCropY, ctx->out_file);
    }

    if (!ctx->cli_options->dump_monochrome) {
        for (int i = top/2; i < p->heightC - bottom/2; i++) {
            fwrite(&p->cb[i*p->widthC + left/2], 1, p->widthCropC, ctx->out_file);
        }
        for (int i = top/2; i < p->heightC - bottom/2; i++) {
            fwrite(&p->cr[i*p->widthC + left/2], 1, p->widthCropC, ctx->out_file);
        }
    }
}

void pic_pool_init(PicturePool *pool, const Undo264Context *ctx) {
    SPS *sps = ctx->ps->sps;
    int num_mbs = sps->pic_width_in_mbs * sps->pic_height_in_map_units;

    pool->size = ctx->dpb->slots_size + 2;
    pool->nb_available = pool->size;

    if (!ctx->mb_metadata_initialized || num_mbs != ctx->num_mbs) {
        if (ctx->mb_metadata_initialized) { /* will have to reallocate the buffers, shouldn't happen mid-stream */
            decoder_free_metadata(ctx);
        }
        decoder_alloc_metadata(ctx);
    }

    pool->slots = calloc_check(pool->size, sizeof(Picture*), ctx);

    pool->available = calloc_check(pool->size, sizeof(bool), ctx);

    for (int i = 0; i < pool->size; i++) {
        pool->slots[i] = picture_alloc(sps, ctx);
        if (sps->chroma_format_idc == 0) {
            Picture *pic = pool->slots[i];
            memset(pic->cb, 128u, pic->widthC * pic->heightC);
            memset(pic->cr, 128u, pic->widthC * pic->heightC);
        }

    }
    memset(pool->available, true, pool->size);
    pool->nb_available = pool->size;
}

void pic_pool_free(PicturePool *pool, const Undo264Context *ctx) {
    for (int i = 0; i < pool->size; i++) {
        if (pool->slots[i]) picture_free(pool->slots[i]);
    }
    free(pool->slots);
    free(pool->available);
    free(pool);
}

Picture *pic_pool_get(PicturePool *pool) {
    for (int i = 0; i < pool->size; i++) {
        if (pool->available[i]) {
            pool->available[i] = false;
            pool->nb_available--;
            return pool->slots[i];
        }
    }
    return NULL; // bug
}

void pic_pool_getback(Picture *pic, PicturePool *pool) {
    int idx = -1;
    for (int i = 0; i < pool->size; i++) {
        if (pool->slots[i] == pic) {
            idx = i;
            break;
        }
    }
    if (idx != -1) {
        pool->available[idx] = true;
        pool->nb_available++;
    }
}