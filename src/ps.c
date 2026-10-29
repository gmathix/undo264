//
// Created by gmathix on 3/12/26.
//




#include "global.h"

#include "ps.h"

#include <unistd.h>

#include "dequant.h"
#include "error.h"
#include "util/expgolomb.h"







#define MIN_LOG2_MAX_FRAME_NUM    4
#define MAX_LOG2_MAX_FRAME_NUM    (12 + MIN_LOG2_MAX_FRAME_NUM)



/* 7.3.2.1.1 */
int decode_sps(Undo264Context *ctx) {
    #ifdef SLICES_LOG
        printf("READING SPS\n");
    #endif


    BitReader *br = ctx->br;
    ParamSets *ps = ctx->ps;


    SPS *sps = calloc_check(1, sizeof(SPS), ctx);


    int profile_idc = read_u(br, 8);

    sps->constraint_set0_flag = read_u(br, 1);
    sps->constraint_set1_flag = read_u(br, 1);
    sps->constraint_set2_flag = read_u(br, 1);
    sps->constraint_set3_flag = read_u(br, 1);
    sps->constraint_set4_flag = read_u(br, 1);
    sps->constraint_set5_flag = read_u(br, 1);

    bitreader_skip_bits(br, 2); // reserved zero bits

    sps->level_idc = read_u(br, 8);


    sps->sps_id = read_ue(br);
    if (sps->sps_id >= MAX_SPS_COUNT) {
        free(sps);
        DEC_FAIL(ctx, UNDO_ERR_BITSTREAM,
            "sps_id %u out of range, max is %d", sps->sps_id, MAX_SPS_COUNT);
    }

    free(ps->sps_list[sps->sps_id]);
    ps->sps_list[sps->sps_id] = sps;
    ps->sps = sps;


    DEC_CHECK(ctx,
        (profile_idc == PROFILE_BASELINE || profile_idc == PROFILE_MAIN ||
         profile_idc == PROFILE_EXTENDED || profile_idc == PROFILE_HIGH),
         UNDO_ERR_UNSUPPORTED,
         "profile_idc %d (%s) is not supported", profile_idc, profile_to_string(profile_idc));


    sps->profile_idc = profile_idc;


    infer_flat_matrices(true, ctx);

    /* not going to handle these for now. focusing on Baseline + Main profiles
     * probably won't even ever work on these */
    if (sps->profile_idc == PROFILE_HIGH ||
        sps->profile_idc == PROFILE_HIGH_10 ||
        sps->profile_idc == PROFILE_HIGH_422 ||
        sps->profile_idc == PROFILE_HIGH_PRED_444 ||
        sps->profile_idc == PROFILE_CAVLC_444  ||
        sps->profile_idc == 83  || // Scalable High Intra profile (SVC)
        sps->profile_idc == 86  || // Stereo High profile (MVC)
        sps->profile_idc == 118 || // Multiview Depth High profile (MVCD)
        sps->profile_idc == 128)   // old High444 profile
    {
        sps->chroma_format_idc = read_ue(br);
        if (sps->chroma_format_idc == 3) {
            sps->separate_color_plane_flag = read_u(br, 1);
            /// TODO: something
        }

        sps->bit_depth_luma_minus8 = read_ue(br);
        sps->bit_depth_chroma_minus8 = read_ue(br);

        uint32_t transform_bypass = read_u(br, 1);

        uint32_t scaling_matrix_present = read_u(br, 1);
        ctx->seqScalingMatrixPresent = scaling_matrix_present;
        if (scaling_matrix_present) {
            for (int i = 0; i < 8; i++) {
                bool present = read_u(br, 1);
                if (present) {
                    if (i < 6) {
                        parse_scaling_list(ctx->seqScalingList4x4[i], 16, &ctx->useDefaultList4x4[i], br);
                        if (ctx->useDefaultList4x4[i]) {
                            scaling_list_fallback(i, true, false, ctx);
                        }
                    } else {
                        parse_scaling_list(ctx->seqScalingList8x8[i-6], 64, &ctx->useDefaultList8x8[i-6], br);
                        if (ctx->useDefaultList8x8[i-6]) {
                            scaling_list_fallback(i, false, false, ctx);
                        }
                    }
                } else {
                    scaling_list_fallback(i, i < 6, false, ctx);
                }
            }
        }

    } else {
        sps->chroma_format_idc = 1;
        sps->bit_depth_luma = 8;
        sps->bit_depth_chroma = 8;
    }

    precompute_4x4_scales(ctx->seqScalingList4x4, ctx);
    precompute_8x8_scales(ctx->seqScalingList8x8, ctx);


    sps->log2_max_frame_num_minus4 =read_ue(br);
    DEC_CHECK(ctx,
        (sps->log2_max_frame_num_minus4 >= MIN_LOG2_MAX_FRAME_NUM - 4 &&
        sps->log2_max_frame_num_minus4 <= MAX_LOG2_MAX_FRAME_NUM),
        UNDO_ERR_BITSTREAM,
        "log2_max_frame_num_minus4 out of range(0-12): %d\n", sps->log2_max_frame_num_minus4);

    ctx->maxFrameNum = 1 << (sps->log2_max_frame_num_minus4 + 4);

    sps->poc_type = read_ue(br);
    if (sps->poc_type == 0) {
        sps->log2_max_poc_lsb_minus4 = read_ue(br);
    } else if (sps->poc_type == 1) {
        sps->delta_pic_order_always_zero_flag      = read_u(br, 1);
        sps->offset_for_non_ref_pic                = read_se(br);
        sps->offset_for_top_to_bottom_field        = read_se(br);
        sps->num_ref_frames_in_poc_cycle = read_ue(br);

        sps->offset_for_ref_frame = calloc_check(sps->num_ref_frames_in_poc_cycle, sizeof(int32_t), ctx);
        for (int i = 0; i < sps->num_ref_frames_in_poc_cycle; i++) {
            sps->offset_for_ref_frame[i] = read_se(br);
        }
    } else if (sps->poc_type != 2) {
        DEC_FAIL(ctx, UNDO_ERR_BITSTREAM, "invalid poc type: %d\n", sps->poc_type);
    }


    

    sps->max_num_ref_frames = read_ue(br);
    sps->gaps_in_frame_num_allowed_flag = read_u(br, 1);
    
    
    sps->pic_width_in_mbs_minus1 = read_ue(br);
    sps->pic_height_in_map_units_minus1 = read_ue(br);
    sps->frame_mbs_only_flag = read_u(br, 1);

    DEC_CHECK(ctx, sps->frame_mbs_only_flag, UNDO_ERR_UNSUPPORTED,
        "field coding is not supported");



    sps->direct_8x8_inference_flag = read_u(br, 1);
    sps->frame_cropping_flag = read_u(br, 1);
    if (sps->frame_cropping_flag) {
        uint32_t crop_left   = read_ue(br);
        uint32_t crop_right  = read_ue(br);
        uint32_t crop_top    = read_ue(br);
        uint32_t crop_bottom = read_ue(br);
        uint32_t width  = 16 * (sps->pic_width_in_mbs_minus1 + 1);
        uint32_t height = 16 * (sps->pic_height_in_map_units_minus1 + 1);

        int vsub = (sps->chroma_format_idc == 1) ? 1 : 0;
        int hsub = (sps->chroma_format_idc == 1 ||
                    sps->chroma_format_idc == 2) ? 1 : 0;
        int step_x = 1 << hsub;
        int step_y = (2 - sps->frame_mbs_only_flag) << vsub;

        if (crop_left   > (unsigned)INT_MAX / 4 / step_x ||
            crop_right  > (unsigned)INT_MAX / 4 / step_x ||
            crop_top    > (unsigned)INT_MAX / 4 / step_y ||
            crop_bottom > (unsigned)INT_MAX / 4 / step_y ||
            (crop_left + crop_right ) * step_x >= width ||
            (crop_top  + crop_bottom) * step_y >= height)
        {
            DEC_FAIL(ctx, UNDO_ERR_BITSTREAM,
                "crop values invalid %d %d %d %d / %d %d\n", crop_left, crop_right, crop_top, crop_bottom, width, height);
        }

        sps->crop_left_offset   = crop_left * step_x;
        sps->crop_right_offset  = crop_right * step_x;
        sps->crop_top_offset    = crop_top * step_y;
        sps->crop_bottom_offset = crop_bottom * step_y;
    } else {
        sps->crop_left_offset   =
        sps->crop_right_offset  =
        sps->crop_top_offset    =
        sps->crop_bottom_offset = 0;
    }


    /* unused for now */
    int vui_params_present = read_u(br, 1);
    if (vui_params_present) {
        decode_vui(sps, ctx);
    }


    // derive params
    sps->bit_depth_luma             = sps->bit_depth_luma_minus8 + 8;
    sps->bit_depth_chroma           = sps->bit_depth_chroma_minus8 + 8;
    sps->log2_max_frame_num         = sps->log2_max_frame_num_minus4 + 4;
    sps->log2_max_poc_lsb = sps->log2_max_poc_lsb_minus4 + 4;
    sps->pic_width_in_mbs           = sps->pic_width_in_mbs_minus1 + 1;
    sps->pic_height_in_map_units    =
        (sps->pic_height_in_map_units_minus1 + 1) * (2 - sps->frame_mbs_only_flag);
    sps->pic_width_samples_l        = sps->pic_width_in_mbs * 16 ;
    sps->pic_height_samples_l       = sps->pic_height_in_map_units * 16;


    return 0;
}


/* 7.3.2.2 */
int decode_pps(Undo264Context *ctx) {
    #ifdef SLICES_LOG
        printf("READING PPS\n");
    #endif

    BitReader *br = ctx->br;
    ParamSets *ps = ctx->ps;

    PPS *pps = calloc_check(1, sizeof(PPS), ctx);


    pps->pps_id = read_ue(br);
    if (pps->pps_id > MAX_PPS_COUNT) {
        free(pps);
        DEC_FAIL(ctx, UNDO_ERR_BITSTREAM, "pps id out of range 0-%d: %d\n", MAX_PPS_COUNT, pps->pps_id);
    }

    free(ps->pps_list[pps->pps_id]);
    ps->pps_list[pps->pps_id] = pps;
    ps->pps = pps;


    pps->sps_id = read_ue(br);
    DEC_CHECK(ctx, (pps->sps_id < MAX_SPS_COUNT && ps->sps_list[pps->sps_id]),
        UNDO_ERR_BITSTREAM, "invalid sps referenced : %d\n", pps->sps_id);

    pps->cabac_flag = read_u(br, 1);

    pps->bottom_field_pic_order_in_frame_present_flag = read_u(br, 1);
    pps->num_slice_groups_minus1 = read_ue(br);

    DEC_CHECK(ctx, pps->num_slice_groups_minus1 == 0, UNDO_ERR_UNSUPPORTED,
        "mutliple slice groups not supported");



    pps->num_ref_idx_l0_default_active_minus1      = read_ue(br);
    pps->num_ref_idx_l1_default_active_minus1      = read_ue(br);
    pps->weighted_pred_flag                        = read_u(br, 1);
    pps->weighted_bipred_idc                       = read_u(br, 2);
    pps->pic_init_qp_minus26                       = read_se(br);
    pps->pic_init_qs_minus26                       = read_se(br);
    pps->chroma_qp_index_offset                    = read_se(br);
    pps->deblocking_filter_control_present_flag    = read_u(br, 1);
    pps->constrained_intra_pred_flag               = read_u(br, 1);
    pps->redundant_pic_cnt_present_flag            = read_u(br, 1);


    for (int i = 0; i < 6; i++) {
        memcpy(ctx->scalingList4x4[i], ctx->seqScalingList4x4[i], 16 * sizeof(int16_t));
    }
    for (int i = 0; i < 2; i++) {
        memcpy(ctx->scalingList8x8[i], ctx->seqScalingList8x8[i], 64 * sizeof(int16_t));
    }

    if (more_rbsp_data(br)) {
        pps->transform_8x8_mode_flag = read_u(br, 1);
        bool pic_scaling_matrix_present = read_u(br, 1);
        if (pic_scaling_matrix_present) {
            for (int i = 0; i < 6 + 2*pps->transform_8x8_mode_flag; i++) {
                bool present = read_u(br, 1);
                if (present) {
                    if (i < 6) {
                        parse_scaling_list(ctx->scalingList4x4[i], 16, &ctx->useDefaultList4x4[i], br);
                        if (ctx->useDefaultList4x4[i]) {
                            scaling_list_fallback(i, true, true, ctx);
                        }
                    } else {
                        parse_scaling_list(ctx->scalingList8x8[i-6], 64, &ctx->useDefaultList8x8[i-6], br);
                        if (ctx->useDefaultList8x8[i-6]) {
                            scaling_list_fallback(i, false, true, ctx);
                        }
                    }
                } else {
                    scaling_list_fallback(i, i < 6, true, ctx);
                }
            }

        }
        pps->second_chroma_qp_index_offset = read_se(br);
    } else {
        pps->second_chroma_qp_index_offset = pps->chroma_qp_index_offset;
    }

    precompute_4x4_scales(ctx->scalingList4x4, ctx);
    precompute_8x8_scales(ctx->scalingList8x8, ctx);


    /* derive */
    pps->num_slice_groups              = pps->num_slice_groups_minus1 + 1;
    pps->num_ref_idx_l0_default_active = pps->num_ref_idx_l0_default_active_minus1 + 1;
    pps->num_ref_idx_l1_default_active = pps->num_ref_idx_l1_default_active_minus1 + 1;
    pps->pic_init_qp                   = pps->pic_init_qp_minus26 + 26;
    pps->pic_init_qs                   = pps->pic_init_qs_minus26 + 26;

    return 0;
}


/* E.1.1 */
int decode_vui (SPS *sps, Undo264Context *ctx) {
    BitReader *br = ctx->br;

    /* not used for now, just necessary parsing for bitreader alignement with the reference decoder */

    int aspect_ratio_present_flag = read_u(br, 1);

    if (aspect_ratio_present_flag) {
        int aspect_ratio_idc = read_u(br, 8);

    }


    int overscan_info_present_flag = read_u(br, 1);
    if (overscan_info_present_flag) {
        int overscan_appropriate_flag = read_u(br, 1);

    }


    int video_signal_type_present_flag = read_u(br, 1);
    if (video_signal_type_present_flag) {
        int video_format = read_u(br, 3);
        int video_full_range_flag = read_u(br, 1);
        int color_description_present_flag = read_u(br, 1);
        if (color_description_present_flag) {
            int color_primaries = read_u(br, 8);
            int transfer_characteristics = read_u(br, 8);
            int matrix_coeffs = read_u(br, 8);
        }
    }

    int chroma_loc_info_present_flag = read_u(br, 1);
    if (chroma_loc_info_present_flag) {
        uint32_t chroma_sample_loc_type_top_field = read_ue(br);
        uint32_t chroma_sample_log_tyep_bottom_field = read_ue(br);
    }

    int timing_info_present_flag = read_u(br, 1);
    if (timing_info_present_flag) {
        uint32_t num_units_in_ticks = read_u(br, 32);
        uint32_t time_scale = read_u(br, 32);
        uint32_t fixed_frame_rate_flag = read_u(br, 1);
    }

    int nal_hrd_parameters_present_flag = read_u(br, 1);
    if (nal_hrd_parameters_present_flag) {
        /* hrd_parameters() */
    }


    int vcl_hrd_parameters_present_flag = read_u(br, 1);
    if (vcl_hrd_parameters_present_flag) {
        /* hrd_parameters() */
    }


    if (nal_hrd_parameters_present_flag || vcl_hrd_parameters_present_flag) {
        int low_delay_hrd_flag = read_u(br, 1);
    }

    int pic_struct_present_flag = read_u(br, 1);
    int bitstream_restriction_flag = read_u(br, 1);
    if (bitstream_restriction_flag) {
        int mvs_over_pic_boundaries_flag = read_u(br, 1);
        uint32_t max_bytes_per_pic_denom = read_ue(br);
        uint32_t max_bits_per_mb_denom = read_ue(br);
        uint32_t log2_max_mb_length_horizontal = read_ue(br);
        uint32_t log2_max_mv_length_vertical = read_ue(br);
        uint32_t max_num_reorder_frames = read_ue(br);
        sps->max_dec_frame_buffering = read_ue(br);
    }
}



