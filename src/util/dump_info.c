//
// Created by gmathix on 10/15/26.
//

/*
 * JSON H.264 stream info dumper (only compatible with undo264)
 * written using AI assistance (Claude Sonnet 5.5)
 */

#include "dump_info.h"

#include <stdio.h>
#include <stdlib.h>

#include "mbutil.h"
#include "predutil.h"
#include "slice.h"



static const char *mb_types_code[] = {
    "Intra 4x4",
    "Intra 8x8",
    "Intra 16x16",
    "PCM",
    "Skip",
    "Direct",
    "Inter 16x16",
    "Inter 16x8",
    "Inter 8x16",
    "Inter 8x8"
};
static int mb_type_to_code(int type) {
    if (IS_INTRA4x4   (type))  return 0;
    if (IS_INTRA8x8   (type))  return 1;
    if (IS_INTRA16x16 (type))  return 2;
    if (IS_PCM        (type))  return 3;
    if (IS_SKIP       (type))  return 4;
    if (IS_DIRECT     (type))  return 5;
    if (IS_16x16      (type))  return 6;
    if (IS_16x8       (type))  return 7;
    if (IS_8x16       (type))  return 8;
    if (IS_8x8        (type))  return 9;
    return -1;
}

static const char *sub_mb_types_code[] = {
    "Direct",
    "8x8",
    "8x4",
    "4x8",
    "4x4"
};
static int sub_mb_type_to_code(int type) {
    if (IS_SUB_DIRECT(type)) return 0;
    if (IS_SUB_8x8(type))    return 1;
    if (IS_SUB_8x4(type))    return 2;
    if (IS_SUB_4x8(type))    return 3;
    if (IS_SUB_4x4(type))    return 4;
    return -1;
}

static const char *intra_pred_4x4_8x8_mode_code[] = {
    "Vertical",
    "Horizontal",
    "DC",
    "Diagonal down left",
    "Diagonal down right",
    "Vertical right",
    "Horizontal down",
    "Vertical left",
    "Horizontal up"
};
static const char *intra_pred_16x16_mode_code[] = {
    "Vertical",
    "Horizontal",
    "DC",
    "Plane"
};
static const char *intra_pred_chroma_mode_code[] = {
    "DC",
    "Horizontal",
    "Vertical",
    "Plane"
};




static int pic_dec_idx(const Picture *pic) {
    return pic && pic != &EMPTY_PICTURE ? (int)pic->dec_idx : -1;
}

// dec_idx of the picture referenced by ref_idx in the current slice's list, -1 if there is none
static int ref_dec_idx(const DPB *dpb, int list, int ref_idx) {
    if (ref_idx < 0 || (size_t)(ref_idx + 1) >= MAX_NUM_REF_PICTURES) return -1;
    return pic_dec_idx(dpb->refs[list][ref_idx + 1]);
}

// appends one complete record followed by '\n'
static int emit_line(StrBuf *sb, FILE *file) {
    if (!sb_ok(sb) || !file) return -1;
    sb_putc(sb, '\n');
    if (!sb_ok(sb)) return -1;
    return fwrite(sb->data, 1, sb->len, file) == sb->len ? 0 : -1;
}

// every record is {"type": <name>, "content": { ... }}
static void begin_record(StrBuf *sb, const char *type) {
    sb_open(sb, NULL, '{');
    sb_kv_str(sb, "type", type);
    sb_open(sb, "content", '{');
}
static void end_record(StrBuf *sb) {
    sb_close(sb, '}');
    sb_close(sb, '}');
}





#define PAYLOAD_BUFFERS(X)                                                                              \
    X(slices) X(dpb_slots) X(dpb_l0) X(dpb_l1) X(mb_type) X(sub_mb_type) \
    X(qpy) X(cbp) X(t8x8) X(intra_modes) X(chroma_intra_modes) X(pred_dir) X(mv_l0) X(mv_l1) X(ref_dec_idx_l0) \
    X(ref_dec_idx_l1)

PictureJSONPayload *make_picture_json_payload(Undo264Context *ctx) {
    PictureJSONPayload *payload = calloc_check(1, sizeof(PictureJSONPayload), ctx);
    if (!payload) return NULL;

#define X(name) sb_init(&payload->name);
    PAYLOAD_BUFFERS(X)
#undef X
    return payload;
}

void reset_picture_json_payload(PictureJSONPayload *payload) {
    if (!payload) return;
    payload->decoded_idx = payload->output_idx = payload->poc = payload->frame_num = 0;
    payload->idr = false;

#define X(name) sb_clear(&payload->name);
    PAYLOAD_BUFFERS(X)
#undef X
}

void free_picture_json_payload(PictureJSONPayload *payload) {
    if (!payload) return;

#define X(name) sb_free(&payload->name);
    PAYLOAD_BUFFERS(X)
#undef X
    free(payload);
}


/* header records: info, SPS, PPS */

static void put_string_array(StrBuf *sb, const char *key, const char **names, size_t n) {
    sb_open(sb, key, '[');
    for (size_t i = 0; i < n; i++)
        sb_val_str(sb, names[i]);
    sb_close(sb, ']');
}

void write_info_header_json(const Undo264Context *ctx) {
    StrBuf sb;
    sb_init(&sb);

    begin_record(&sb, "info");

    sb_kv_str(&sb, "schema_version", "1.0");

    sb_open   (&sb, "decoding_cli_options", '{');
    sb_kv_bool(&sb, "--monochrome",  ctx->cli_options->dump_monochrome);
    sb_kv_bool(&sb, "--dump-frames", ctx->cli_options->dump_frames);
    sb_kv_bool(&sb, "--dump-info",   ctx->cli_options->dump_info);
    sb_kv_bool(&sb, "--no-deblock", !ctx->cli_options->deblock);
    sb_kv_bool(&sb, "--no-simd",    !ctx->cli_options->use_simd);
    sb_kv_bool(&sb, "--no-sse",     !ctx->cli_options->use_sse);
    sb_kv_bool(&sb, "--no-avx2",    !ctx->cli_options->use_avx2);
    sb_close(&sb, '}');

    put_string_array(&sb, "mb_types_code",                mb_types_code,                10);
    put_string_array(&sb, "sub_mb_types_code",            sub_mb_types_code,             5);
    put_string_array(&sb, "intra_pred_4x4_8x8_mode_code", intra_pred_4x4_8x8_mode_code,  9);
    put_string_array(&sb, "intra_pred_16x16_mode_code",   intra_pred_16x16_mode_code,    4);
    put_string_array(&sb, "intra_pred_chroma_mode_code",  intra_pred_chroma_mode_code,   4);

    end_record(&sb);
    emit_line(&sb, ctx->info_out_file);
    sb_free(&sb);
}

void write_sps_json(const SPS *sps, const Undo264Context *ctx) {
    StrBuf sb; sb_init(&sb);
    begin_record(&sb, "sps");

    sb_kv_str (&sb, "profile",                 profile_to_string(sps->profile_idc));
    sb_kv_int (&sb, "level_idc",                 sps->level_idc);
    sb_kv_int (&sb, "sps_id",                    sps->sps_id);
    sb_kv_int (&sb, "max_frame_num",             1 << sps->log2_max_frame_num);
    sb_kv_int (&sb, "poc_type",                  sps->poc_type);
    if (sps->poc_type == 0)
        sb_kv_int(&sb, "max_pic_order_cnt_lsb",  1 << sps->log2_max_poc_lsb);
    sb_kv_int (&sb, "max_num_ref_frames",        sps->max_num_ref_frames);
    sb_kv_bool(&sb, "gaps_in_frame_num_allowed", sps->gaps_in_frame_num_allowed_flag);
    sb_kv_int (&sb, "pic_width_in_mbs",          sps->pic_width_in_mbs);
    sb_kv_int (&sb, "pic_height_in_mbs",         sps->pic_height_in_map_units);
    sb_kv_int (&sb, "pic_height_luma",           sps->pic_height_samples_l);
    sb_kv_int (&sb, "pic_width_luma",            sps->pic_width_samples_l);
    sb_kv_bool(&sb, "direct_8x8_inference",      sps->direct_8x8_inference_flag);
    sb_kv_bool(&sb, "frame_cropping_flag",       sps->frame_cropping_flag);
    if (sps->frame_cropping_flag) {
        sb_kv_int(&sb, "frame_crop_left_offset_samples",   sps->crop_left_offset);
        sb_kv_int(&sb, "frame_crop_right_offset_samples",  sps->crop_right_offset);
        sb_kv_int(&sb, "frame_crop_top_offset_samples",    sps->crop_top_offset);
        sb_kv_int(&sb, "frame_crop_bottom_offset_samples", sps->crop_bottom_offset);
    }

    end_record(&sb);
    emit_line(&sb, ctx->info_out_file);
    sb_free(&sb);
}

void write_pps_json(const PPS *pps, const Undo264Context *ctx) {
    StrBuf sb; sb_init(&sb);
    begin_record(&sb, "pps");

    sb_kv_int (&sb, "pps_id",                            pps->pps_id);
    sb_kv_int (&sb, "sps_id",                            pps->sps_id);
    sb_kv_str (&sb, "entropy_coding_mode",             pps->cabac_flag ? "CABAC" : "CAVLC");
    sb_kv_int (&sb, "num_ref_idx_l0_default_active",     pps->num_ref_idx_l0_default_active);
    sb_kv_int (&sb, "num_ref_idx_l1_default_active",     pps->num_ref_idx_l1_default_active);
    sb_kv_bool(&sb, "weighted_pred",                     pps->weighted_pred_flag);
    sb_kv_int (&sb, "weighted_bipred_idc",               pps->weighted_bipred_idc);
    sb_kv_int (&sb, "pic_init_qp",                       pps->pic_init_qp);
    sb_kv_int (&sb, "pic_init_qs",                       pps->pic_init_qs);
    sb_kv_int (&sb, "chroma_qp_index_offset",            pps->chroma_qp_index_offset);
    sb_kv_bool(&sb, "deblocking_filter_control_present", pps->deblocking_filter_control_present_flag);
    sb_kv_bool(&sb, "constrained_intra_pred",            pps->constrained_intra_pred_flag);
    sb_kv_bool(&sb, "redundant_pic_cnt_present",         pps->redundant_pic_cnt_present_flag);
    sb_kv_bool(&sb, "transform_8x8_mode",                pps->transform_8x8_mode_flag);
    sb_kv_int (&sb, "second_chroma_qp_index_offset",     pps->second_chroma_qp_index_offset);

    end_record(&sb);
    emit_line(&sb, ctx->info_out_file);
    sb_free(&sb);
}


/* frame record : header, slices, DPB status */

void write_dpb_status_json(const DPB *dpb, PictureJSONPayload *payload) {
    /* a picture with several slices calls this once per slice: keep only the latest state
     * instead of appending copy to the arrays */
    payload->dpb_slots_size = dpb->slots_size;
    payload->dpb_lists_size = MAX_NUM_REF_PICTURES;
    sb_clear(&payload->dpb_slots);
    sb_clear(&payload->dpb_l0);
    sb_clear(&payload->dpb_l1);



    for (int i = 0; i < dpb->slots_size; i++)
        sb_val_int(&payload->dpb_slots, pic_dec_idx(dpb->slots[i]));

    for (size_t i = 0; i < MAX_NUM_REF_PICTURES; i++) {
        sb_val_int(&payload->dpb_l0, pic_dec_idx(dpb->refs[L0][1+i]));
        sb_val_int(&payload->dpb_l1, pic_dec_idx(dpb->refs[L1][1+i]));
    }
}

void write_frame_header_json(const Picture *pic, PictureJSONPayload *payload) {
    payload->decoded_idx = (int)pic->dec_idx;
    payload->output_idx  = (int)pic->output_idx;
    payload->poc         = (int)pic->poc;
    payload->frame_num   = (int)pic->frame_num;
    payload->idr         = (int)pic->sh->idr_pic_flag;
}

void write_slice_header_json(const SliceHeader *sh, PictureJSONPayload *payload) {
    StrBuf *sb = &payload->slices;
    sb_open(sb, NULL, '{');

    sb_kv_int (sb, "first_mb",   sh->first_mb);
    sb_kv_str (sb, "slice_type", slice_type_to_string(sh->slice_type));
    sb_kv_int (sb, "pps_id",     sh->pps_id);
    sb_kv_int (sb, "frame_num",  sh->frame_num);
    sb_kv_bool(sb, "idr_pic",    sh->idr_pic_flag);
    if (sh->idr_pic_flag)
        sb_kv_int(sb, "idr_pic_id", sh->idr_pic_id);
    if (sh->sps->poc_type == 0)
        sb_kv_int(sb, "poc_lsb", sh->poc_lsb);
    if (IS_B_SLICE(sh->slice_type))
        sb_kv_str(sb, "direct_mv_pred_mode", sh->direct_spatial_mv_pred_flag ? "spatial" : "temporal");
    if (IS_P_SLICE(sh->slice_type) || IS_B_SLICE(sh->slice_type)) {
        sb_kv_bool(sb, "num_ref_idx_active_override", sh->num_ref_idx_active_override_flag);
        if (sh->num_ref_idx_active_override_flag) {
            sb_kv_int(sb, "num_ref_idx_l0_active", sh->num_ref_idx_l0_active_minus1 + 1);
            if (IS_B_SLICE(sh->slice_type))
                sb_kv_int(sb, "num_ref_idx_l1_active", sh->num_ref_idx_l1_active_minus1 + 1);
        }
    }
    if (sh->pps->cabac_flag && !IS_I_SLICE(sh->slice_type))
        sb_kv_int(sb, "cabac_init_idc", sh->cabac_init_idc);

    sb_kv_int(sb, "slice_qp", sh->pps->pic_init_qp + sh->slice_qp_delta);

    if (sh->pps->deblocking_filter_control_present_flag) {
        sb_kv_int(sb, "disable_deblocking_filter_idc", sh->disable_deblocking_filter_idc);
        if (sh->disable_deblocking_filter_idc != 1) {
            sb_kv_int(sb, "slice_alpha_c0_offset", sh->slice_alpha_c0_offset_div2 * 2);
            sb_kv_int(sb, "slice_beta_offset",     sh->slice_beta_offset_div2 * 2);
        }
    }

    sb_close(sb, '}');
}


/* macroblock data */

void write_macroblock_json(const Macroblock *mb, const Picture *pic, const Undo264Context *ctx, PictureJSONPayload *payload) {
    const MacroblockMetadata *meta = &ctx->mb_metadata[mb->mbAddr];
    const int code = mb_type_to_code(mb->mb_type);

    // mb type
    sb_val_int(&payload->mb_type, code);

    // sub-mb types
    sb_open(&payload->sub_mb_type, NULL, '[');
    for (int sub = 0; sub < 4; sub++) {
        sb_val_int(&payload->sub_mb_type,
                   IS_8x8(mb->mb_type) ? sub_mb_type_to_code(mb->u.pb.sub_mb_info[sub].type) : -1);
    }
    sb_close(&payload->sub_mb_type, ']');

    // qpy, cbp, t8x8
    sb_val_int(&payload->qpy,  meta->QPY);
    sb_val_int(&payload->cbp,  (meta->cbp_chroma << 4) | meta->cbp_luma);
    sb_val_int(&payload->t8x8, meta->t_8x8_flag);

    // intra pred modes: 1 value (16x16), 4 (8x8), 16 (4x4), none otherwise
    sb_open(&payload->intra_modes, NULL, '[');
    if (IS_INTRA16x16(mb->mb_type)) {
        sb_val_int(&payload->intra_modes, meta->intra_16x16_pred_mode);
    } else if (IS_INTRANxN(mb->mb_type)) {
        const int n = IS_INTRA4x4(mb->mb_type) ? 16 : 4;
        for (int i = 0; i < n; i++)
            sb_val_int(&payload->intra_modes, meta->intra_NxN_pred_mode[i]);
    }
    sb_close(&payload->intra_modes, ']');

    sb_val_int(&payload->chroma_intra_modes,
               IS_INTRA(mb->mb_type) && !IS_PCM(mb->mb_type) ? meta->intra_chroma_pred_mode : -1);

    /* inter data. per 8x8 partition : prediction direction and referenced picture
     *             per 4x4 block (subpart) : in luma4x4BlkIdx order, the motion vector of each list, or [] if the list is unused
     */
    StrBuf *dirs    =   &payload->pred_dir;
    StrBuf *mvs[2]  = { &payload->mv_l0,          &payload->mv_l1 };
    StrBuf *refs[2] = { &payload->ref_dec_idx_l0, &payload->ref_dec_idx_l1 };

    sb_open(dirs,    NULL, '[');
    sb_open(mvs[0],  NULL, '['); sb_open( mvs[1], NULL, '[');
    sb_open(refs[0], NULL, '['); sb_open(refs[1], NULL, '[');

    if (IS_INTER(mb->mb_type)) {
        for (int part = 0; part < 4; part++) {
            bool used[2] = {
                pic->pred_flags[mb->mbAddr][L0][part],
                pic->pred_flags[mb->mbAddr][L1][part],
            };

            sb_val_int(dirs, used[0] | used[1] << 1);

            for (int list = 0; list < 2; list++) {
                int ref = -1;
                if (used[list]) {
                    int ref_idx = pic->ref_idx[mb->mbAddr][list][map_4x4[part * 4]];
                    ref = ref_dec_idx(ctx->dpb, list, ref_idx);
                }
                sb_val_int(refs[list], ref);

                for (int subPart = 0; subPart < 4; subPart++) {
                    int pos4x4 = map_4x4[part * 4 + subPart];
                    sb_open(mvs[list], NULL, '[');
                    if (used[list]) {
                        sb_val_int(mvs[list], pic->motion_val[mb->mbAddr][list][pos4x4][0]);
                        sb_val_int(mvs[list], pic->motion_val[mb->mbAddr][list][pos4x4][1]);
                    }
                    sb_close(mvs[list], ']');
                }
            }
        }
    }

    sb_close(dirs, ']');
    sb_close(mvs[0],  ']'); sb_close(mvs[1],  ']');
    sb_close(refs[0], ']'); sb_close(refs[1], ']');
}


/* frame record output */

int write_frame_json(const Undo264Context *ctx, const PictureJSONPayload *p) {
    FILE *file = ctx->info_out_file;
    if (!file) return -1;

    // check everything before writing
#define X(name) if (!sb_ok(&p->name)) return -1;
    PAYLOAD_BUFFERS(X)
#undef X

    // the columns were built independently, so write them straight from their own buffers
    StrBuf head; sb_init(&head);
    sb_open(&head, NULL, '{');
    sb_kv_str (&head, "type", "frame");
    sb_open   (&head, "content", '{');
    sb_kv_int (&head, "decoded_idx", p->decoded_idx);
    sb_kv_int (&head, "output_idx",  p->output_idx);
    sb_kv_int (&head, "POC",         p->poc);
    sb_kv_int (&head, "frame_num",   p->frame_num);
    sb_kv_bool(&head, "IDR",         p->idr);
    sb_puts   (&head, ",\"dpb_status\":{");
    sb_kv_int (&head, "slots_size",  p->dpb_slots_size);
    sb_kv_int (&head, "lists_size",  p->dpb_lists_size);
    int rc = sb_ok(&head) ? 0 : -1;

    #define PUT(buf) (fwrite((buf).data ? (buf).data : "", 1, (buf).len, file) == (buf).len ? 0 : -1)
    #define LIT(str) (fputs((str), file) >= 0 ? 0 : -1)
    #define COLUMN(label, buf)\
        do {                  \
            if (rc == 0) {    \
                rc = LIT(label);  \
                rc = PUT(buf);    \
                rc = LIT("]");    \
            } \
        } while (0);

    if (rc == 0) rc = PUT(head);
    sb_free(&head);


    COLUMN(",\"slots\":[",                           p->dpb_slots);
    COLUMN(",\"l0\":[",                             p->dpb_l0);
    COLUMN(",\"l1\":[",                             p->dpb_l1);
    if (rc == 0) rc = LIT("}");
    COLUMN(",\"slices\":[",                         p->slices);
    COLUMN(",\"mb_type\":[",                        p->mb_type);
    COLUMN(",\"sub_mb_type\":[",                    p->sub_mb_type);
    COLUMN(",\"qpy\":[",                            p->qpy);
    COLUMN(",\"cbp\":[",                            p->cbp);
    COLUMN(",\"t8x8\":[",                           p->t8x8);
    COLUMN(",\"intra_modes\":[",                    p->intra_modes);
    COLUMN(",\"chroma_intra_modes\":[",             p->chroma_intra_modes);
    COLUMN(",\"pred_dir\":[",                       p->pred_dir);
    COLUMN(",\"mv_l0\":[",                          p->mv_l0);
    COLUMN(",\"mv_l1\":[",                          p->mv_l1);
    COLUMN(",\"ref_dec_idx_l0\":[",                 p->ref_dec_idx_l0);
    COLUMN(",\"ref_dec_idx_l1\":[",                 p->ref_dec_idx_l1);
    if (rc == 0) rc = LIT("}}\n");

    #undef COLUMN
    #undef LIT
    #undef PUT
    return rc;
}