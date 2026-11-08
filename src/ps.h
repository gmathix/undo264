//
// Created by gmathix on 3/12/26.
//

#ifndef H264_PS_H
#define H264_PS_H





#include "decoder.h"
#include "util/bitreader.h"


#define MAX_SPS_COUNT     32
#define MAX_PPS_COUNT     256



typedef struct VUI {
    unsigned chroma_sample_loc_type_top_field;
    unsigned chroma_sample_loc_type_bottom_field;
    unsigned num_units_in_tick;
    unsigned time_scale;

    int aspect_ratio_info_present_flag;
    int aspect_ratio_idc;
    int overscan_info_present_flag;
    int overscan_appropriate_flag;
    int video_signal_type_present_flag;
    int video_format;
    int video_full_range_flag;
    int colour_description_present_flag;
    int colour_primaries;
    int transfer_characteristics;
    int matrix_coefficients;
    int chroma_loc_info_present_flag;
    int timing_info_present_flag;
    int fixed_frame_rate_flag;
    int nal_hrd_parameters_present_flag;
    int low_delay_hrd_flag;
    int pic_struct_present_flag;
    int bitstream_restriction_flag;
    int motion_vectors_over_pic_boundaries_flag;
    int max_bytes_per_pic_denom;
    int max_bits_per_mb_denom;
    int log2_max_mv_length_horizontal;
    int log2_max_mv_length_vertical;
    int max_num_reorder_frames;
    int max_dec_frame_buffering;
} VUI ;

typedef struct SPS {

    VUI vui;

    // RAW (from bitstream)


    unsigned bit_depth_chroma_minus8;
    unsigned chroma_format_idc;
    unsigned crop_bottom_offset;
    unsigned crop_left_offset;
    unsigned crop_right_offset;
    unsigned crop_top_offset;
    unsigned log2_max_frame_num_minus4;
    unsigned log2_max_poc_lsb_minus4;
    unsigned num_ref_frames_in_poc_cycle;
    unsigned max_num_ref_frames;
    unsigned poc_type;
    unsigned pic_width_in_mbs_minus1;
    unsigned pic_height_in_map_units_minus1;
    unsigned sps_id;

    int  offset_for_non_ref_pic;
    int *offset_for_ref_frame;
    int  offset_for_top_to_bottom_field;

    int  bit_depth_luma_minus8;
    int  delta_pic_order_always_zero_flag;
    int  direct_8x8_inference_flag;
    int  frame_cropping_flag;
    int  frame_mbs_only_flag;
    int  gaps_in_frame_num_allowed_flag;
    int  level_idc;
    int  max_dec_frame_buffering;
    int  mb_aff_flag;
    int  profile_idc; // 66=baseline, 77=main, 100=high
    int  separate_color_plane_flag;
    int  transform_bypass_flag;

    int8_t constraint_set0_flag;
    int8_t constraint_set1_flag;
    int8_t constraint_set2_flag;
    int8_t constraint_set3_flag;
    int8_t constraint_set4_flag;
    int8_t constraint_set5_flag;



    // DERIVED
    int  bit_depth_luma;     // bit_depth_luma_minus8 + 8
    unsigned bit_depth_chroma;   // bit_depth_chroma_minus8 + 8
    unsigned log2_max_frame_num; // log2_max_frame_num_minus4 + 4
    unsigned log2_max_poc_lsb; // log2_max_poc_lbs_minus4 + 4
    unsigned pic_width_in_mbs; // pic_width_in_mbs_minus1 + 1
    unsigned pic_height_in_map_units; // (pic_height_in_map_units_minus1 + 1) * (2 - frame_mbs_only_flag)
    unsigned pic_width_samples_l;
    unsigned pic_height_samples_l;

} SPS ;


typedef struct PPS {
    // RAW (from bitstream)
    unsigned num_ref_idx_l0_default_active_minus1;
    unsigned num_ref_idx_l1_default_active_minus1;
    unsigned num_slice_groups_minus1;
    unsigned pps_id;
    unsigned sps_id;

    int  chroma_qp_index_offset;
    int  second_chroma_qp_index_offset;
    int  pic_init_qp_minus26;
    int  pic_init_qs_minus26;

    int  bottom_field_pic_order_in_frame_present_flag;
    int  constrained_intra_pred_flag;
    int  deblocking_filter_control_present_flag;
    int  cabac_flag;
    int  redundant_pic_cnt_present_flag;
    int  transform_8x8_mode_flag;
    int  weighted_pred_flag;
    int  weighted_bipred_idc;


    // DERIVED
    unsigned num_ref_idx_l0_default_active;
    unsigned num_ref_idx_l1_default_active;
    unsigned num_slice_groups;
    int  pic_init_qp;
    int  pic_init_qs;

} PPS ;


typedef struct ParamSets {
    SPS *sps_list[MAX_SPS_COUNT];
    PPS *pps_list[MAX_PPS_COUNT];

    /* currently active sps and pps */
    SPS *sps;
    PPS *pps;

} ParamSets ;


int  get_profile (ParamSets *ps);
int  decode_sps  (Undo264Context *ctx);
int  decode_pps  (Undo264Context *ctx);
int  decode_vui (SPS *sps, Undo264Context *ctx);

void ps_uninit   (ParamSets *ps);


#endif //H264_PS_H