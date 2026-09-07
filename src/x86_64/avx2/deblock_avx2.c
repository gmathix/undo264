//
// Created by gmathix on 9/3/26.
//



#include <immintrin.h>

#include "loopfilter.h"
#include "global.h"



#define ABS_DIFF_epi8(r0, r1) _mm256_or_si256(_mm256_subs_epu8(r0, r1), _mm256_subs_epu8(r1, r0))
#define ABS_DIFF_epi16(r0, r1) _mm256_or_si256(_mm256_subs_epu16(r0, r1), _mm256_subs_epu16(r1, r0))

#define THRESH_MASK_epi16(a, thresh) _mm256_cmpgt_epi16(thresh, a)
// convert 0xFFFF to 0x0001
#define THRESH_MASK_epi16_SET1(a, thresh) _mm256_subs_epu16(THRESH_MASK_epi16(a, thresh), _mm256_set1_epi16(0xFFFE))

#define CLIP3_epi16(min, max, r) _mm256_max_epi16(min, _mm256_min_epi16(max, r))


#define add16(a, b) _mm256_add_epi16(a, b)
#define sub16(a, b) _mm256_sub_epi16(a, b)
#define mullo16(a, b) _mm256_mullo_epi16(a, b)
#define srai16(a, b) _mm256_srai_epi16(a, b)
#define slli16(a, b) _mm256_slli_epi16(a, b)
#define set1_epi16(a) _mm256_set1_epi16(a)
#define cmpgt16(a, b) _mm256_cmpgt_epi16(a, b)
#define and256(a, b) _mm256_and_si256(a, b)
#define blendv8(a, b, c) _mm256_blendv_epi8(a, b, c)
#define cmplt16(a, b) cmpgt16(b, a)




#define TRANSPOSE8x8_AVX(l0, l1, l2, l3) do {                                                                \
    __m128i shuffle = _mm_set_epi8(15,11,7,3, 14,10,6,2, 13,9,5,1, 12,8,4,0);                            \
                                                                                                         \
    __m128i t0 = _mm_castps_si128(_mm_shuffle_ps(_mm_castsi128_ps(l0),_mm_castsi128_ps(l1),0b10001000)); \
    __m128i t1 = _mm_castps_si128(_mm_shuffle_ps(_mm_castsi128_ps(l2),_mm_castsi128_ps(l3),0b10001000)); \
    __m128i t2 = _mm_castps_si128(_mm_shuffle_ps(_mm_castsi128_ps(l0),_mm_castsi128_ps(l1),0b11011101)); \
    __m128i t3 = _mm_castps_si128(_mm_shuffle_ps(_mm_castsi128_ps(l2),_mm_castsi128_ps(l3),0b11011101)); \
                                                                                                         \
    l0 = _mm_shuffle_epi8(t0, shuffle);                                                                  \
    l1 = _mm_shuffle_epi8(t1, shuffle);                                                                  \
    l2 = _mm_shuffle_epi8(t2, shuffle);                                                                  \
    l3 = _mm_shuffle_epi8(t3, shuffle);                                                                  \
                                                                                                         \
    t0 = _mm_unpacklo_epi32(l0, l1);                                                                     \
    t1 = _mm_unpackhi_epi32(l0, l1);                                                                     \
    t2 = _mm_unpacklo_epi32(l2, l3);                                                                     \
    t3 = _mm_unpackhi_epi32(l2, l3);                                                                     \
                                                                                                         \
    _mm_storeu_si128(&l0, t0);                                                                           \
    _mm_storeu_si128(&l1, t1);                                                                           \
    _mm_storeu_si128(&l2, t2);                                                                           \
    _mm_storeu_si128(&l3, t3);                                                                           \
} while (0);




typedef struct {
    __m128i c0, c1, c2, c3, c4, c5, c6, c7;
} strided_load_avx_t ;

/** load p0,p1,p2,q0,q1,q2 columns in registers
 *
 *  instead of loading the 16x16 block to then do a 16x16 transpose and get p0,p1,p2,q0,q1,q2 as rows,
 *  we instead load the two neighboring 8x8 blocks and transpose them. this should be faster than a 16x16 transform,
 *  which is probably slow here (64 simd ops with avx512, can't imagine with sse)
 */
static always_inline strided_load_avx_t load_strided_avx_16(uint8_t *src, int stride) {
    __m128i l0 = _mm_unpacklo_epi64(_mm_loadu_si64(&src[0*stride]), _mm_loadu_si64(&src[1*stride]));
    __m128i l1 = _mm_unpacklo_epi64(_mm_loadu_si64(&src[2*stride]), _mm_loadu_si64(&src[3*stride]));
    __m128i l2 = _mm_unpacklo_epi64(_mm_loadu_si64(&src[4*stride]), _mm_loadu_si64(&src[5*stride]));
    __m128i l3 = _mm_unpacklo_epi64(_mm_loadu_si64(&src[6*stride]), _mm_loadu_si64(&src[7*stride]));

    TRANSPOSE8x8_AVX(l0, l1, l2, l3)


    __m128i l4 = _mm_unpacklo_epi64(_mm_loadu_si64(&src[ 8*stride]), _mm_loadu_si64(&src[ 9*stride]));
    __m128i l5 = _mm_unpacklo_epi64(_mm_loadu_si64(&src[10*stride]), _mm_loadu_si64(&src[11*stride]));
    __m128i l6 = _mm_unpacklo_epi64(_mm_loadu_si64(&src[12*stride]), _mm_loadu_si64(&src[13*stride]));
    __m128i l7 = _mm_unpacklo_epi64(_mm_loadu_si64(&src[14*stride]), _mm_loadu_si64(&src[15*stride]));

    TRANSPOSE8x8_AVX(l4, l5, l6, l7)

    return (strided_load_avx_t) {
    _mm_unpacklo_epi64(l0, l4),
    _mm_unpackhi_epi64(l0, l4),
    _mm_unpacklo_epi64(l1, l5),
    _mm_unpackhi_epi64(l1, l5),
    _mm_unpacklo_epi64(l2, l6),
    _mm_unpackhi_epi64(l2, l6),
    _mm_unpacklo_epi64(l3, l7),
    _mm_unpackhi_epi64(l3, l7),
    };
}

static always_inline void store_strided_avx_16(uint8_t *dst, int stride, strided_load_avx_t store) {
    __m128i l0 = _mm_unpacklo_epi64(store.c0, store.c1);
    __m128i l1 = _mm_unpacklo_epi64(store.c2, store.c3);
    __m128i l2 = _mm_unpacklo_epi64(store.c4, store.c5);
    __m128i l3 = _mm_unpacklo_epi64(store.c6, store.c7);

    TRANSPOSE8x8_AVX(l0, l1, l2, l3)
    _mm_storeu_si64(&dst[0*stride], _mm_unpacklo_epi64(l0, l0));
    _mm_storeu_si64(&dst[1*stride], _mm_unpackhi_epi64(l0, l0));
    _mm_storeu_si64(&dst[2*stride], _mm_unpacklo_epi64(l1, l1));
    _mm_storeu_si64(&dst[3*stride], _mm_unpackhi_epi64(l1, l1));
    _mm_storeu_si64(&dst[4*stride], _mm_unpacklo_epi64(l2, l2));
    _mm_storeu_si64(&dst[5*stride], _mm_unpackhi_epi64(l2, l2));
    _mm_storeu_si64(&dst[6*stride], _mm_unpacklo_epi64(l3, l3));
    _mm_storeu_si64(&dst[7*stride], _mm_unpackhi_epi64(l3, l3));

    l0 = _mm_unpackhi_epi64(store.c0, store.c1);
    l1 = _mm_unpackhi_epi64(store.c2, store.c3);
    l2 = _mm_unpackhi_epi64(store.c4, store.c5);
    l3 = _mm_unpackhi_epi64(store.c6, store.c7);

    TRANSPOSE8x8_AVX(l0, l1, l2, l3);
    _mm_storeu_si64(&dst[ 8*stride], _mm_unpacklo_epi64(l0, l0));
    _mm_storeu_si64(&dst[ 9*stride], _mm_unpackhi_epi64(l0, l0));
    _mm_storeu_si64(&dst[10*stride], _mm_unpacklo_epi64(l1, l1));
    _mm_storeu_si64(&dst[11*stride], _mm_unpackhi_epi64(l1, l1));
    _mm_storeu_si64(&dst[12*stride], _mm_unpacklo_epi64(l2, l2));
    _mm_storeu_si64(&dst[13*stride], _mm_unpackhi_epi64(l2, l2));
    _mm_storeu_si64(&dst[14*stride], _mm_unpacklo_epi64(l3, l3));
    _mm_storeu_si64(&dst[15*stride], _mm_unpackhi_epi64(l3, l3));
}

static always_inline strided_load_avx_t load_strided_avx_8(uint8_t *src, int stride) {
    __m128i zero_reg = _mm_setzero_si128();
    __m128i l0 = _mm_unpacklo_epi64(_mm_loadu_si64(&src[0*stride]), _mm_loadu_si64(&src[1*stride]));
    __m128i l1 = _mm_unpacklo_epi64(_mm_loadu_si64(&src[2*stride]), _mm_loadu_si64(&src[3*stride]));
    __m128i l2 = _mm_unpacklo_epi64(_mm_loadu_si64(&src[4*stride]), _mm_loadu_si64(&src[5*stride]));
    __m128i l3 = _mm_unpacklo_epi64(_mm_loadu_si64(&src[6*stride]), _mm_loadu_si64(&src[7*stride]));

    TRANSPOSE8x8_AVX(l0, l1, l2, l3)

    // directly convert to epi16
    return (strided_load_avx_t) {
        _mm_unpacklo_epi8(l0, zero_reg),
        _mm_unpackhi_epi8(l0, zero_reg),
        _mm_unpacklo_epi8(l1, zero_reg),
        _mm_unpackhi_epi8(l1, zero_reg),
        _mm_unpacklo_epi8(l2, zero_reg),
        _mm_unpackhi_epi8(l2, zero_reg),
        _mm_unpacklo_epi8(l3, zero_reg),
        _mm_unpackhi_epi8(l3, zero_reg),
    };
}

static always_inline void store_strided_avx_8(uint8_t *dst, int stride, strided_load_avx_t store) {
    __m128i zero_reg = _mm_setzero_si128();
    __m128i l0 = _mm_unpacklo_epi64(_mm_packus_epi16(store.c0, zero_reg), _mm_packus_epi16(store.c1, zero_reg));
    __m128i l1 = _mm_unpacklo_epi64(_mm_packus_epi16(store.c2, zero_reg), _mm_packus_epi16(store.c3, zero_reg));
    __m128i l2 = _mm_unpacklo_epi64(_mm_packus_epi16(store.c4, zero_reg), _mm_packus_epi16(store.c5, zero_reg));
    __m128i l3 = _mm_unpacklo_epi64(_mm_packus_epi16(store.c6, zero_reg), _mm_packus_epi16(store.c7, zero_reg));

    TRANSPOSE8x8_AVX(l0, l1, l2, l3)
    _mm_storeu_si64(&dst[0*stride], l0);
    _mm_storeu_si64(&dst[1*stride], _mm_unpackhi_epi64(l0, zero_reg));
    _mm_storeu_si64(&dst[2*stride], l1);
    _mm_storeu_si64(&dst[3*stride], _mm_unpackhi_epi64(l1, zero_reg));
    _mm_storeu_si64(&dst[4*stride], l2);
    _mm_storeu_si64(&dst[5*stride], _mm_unpackhi_epi64(l2, zero_reg));
    _mm_storeu_si64(&dst[6*stride], l3);
    _mm_storeu_si64(&dst[7*stride], _mm_unpackhi_epi64(l3, zero_reg));
}





void deblock_edge_weak_luma_h_avx2(uint8_t *dst, int stride, int alpha, int beta, int indexA, int *bS) {
    __m256i zero_reg = _mm256_setzero_si256();
    __m256i cliphi_reg = _mm256_set1_epi16(0x00FF);


    __m256i p0 = _mm256_cvtepu8_epi16(_mm_loadu_si128((__m128i*)&dst[-1*stride]));
    __m256i p1 = _mm256_cvtepu8_epi16(_mm_loadu_si128((__m128i*)&dst[-2*stride]));
    __m256i p2 = _mm256_cvtepu8_epi16(_mm_loadu_si128((__m128i*)&dst[-3*stride]));
    __m256i q0 = _mm256_cvtepu8_epi16(_mm_loadu_si128((__m128i*)&dst[ 0*stride]));
    __m256i q1 = _mm256_cvtepu8_epi16(_mm_loadu_si128((__m128i*)&dst[ 1*stride]));
    __m256i q2 = _mm256_cvtepu8_epi16(_mm_loadu_si128((__m128i*)&dst[ 2*stride]));

    __m256i tc0 = _mm256_cvtepu8_epi16(_mm_load_si128((__m128i*)get_tc0_table(bS, indexA)));
    __m256i tc0_neg = sub16(zero_reg, tc0);
    __m256i skip = _mm256_cmpgt_epi16(_mm256_set1_epi16(0x0080), tc0);

    __m256i alpha_reg = set1_epi16(alpha);
    __m256i beta_reg  = set1_epi16(beta);

    __m256i p0q0_mask = cmpgt16(alpha_reg, ABS_DIFF_epi16(p0, q0));
    __m256i p1p0_mask = cmpgt16(beta_reg, ABS_DIFF_epi16(p1, p0));
    __m256i q1q0_mask = cmpgt16(beta_reg, ABS_DIFF_epi16(q1, q0));
    __m256i filter_cond = and256(p0q0_mask, and256(p1p0_mask, and256(q1q0_mask, skip)));

    __m256i aP = ABS_DIFF_epi16(p2, p0);
    __m256i aQ = ABS_DIFF_epi16(q2, q0);

    __m256i treshold = add16(tc0, _mm256_add_epi16(THRESH_MASK_epi16_SET1(aP, beta_reg), THRESH_MASK_epi16_SET1(aQ, beta_reg)));
    __m256i treshold_neg = sub16(zero_reg, treshold);


    __m256i delta = mullo16(_mm256_sub_epi16(q0, p0), _mm256_set1_epi16(1 << 2));
    delta = add16(delta, _mm256_sub_epi16(p1, q1));
    delta = add16(delta, _mm256_set1_epi16(4));
    delta = CLIP3_epi16(treshold_neg, treshold, srai16(delta, 3));

    __m256i add_common = srai16(add16(add16(p0, q0), set1_epi16(1)), 1);

    __m256i p1_add = sub16(add_common, slli16(p1, 1));
    p1_add = CLIP3_epi16(tc0_neg, tc0, srai16(add16(p2, p1_add), 1));
    __m256i p1_res = blendv8(p1, add16(p1, p1_add), cmplt16(aP, beta_reg));

    __m256i q1_add = sub16(add_common, slli16(q1, 1));
    q1_add = CLIP3_epi16(tc0_neg, tc0, srai16(add16(q2, q1_add), 1));
    __m256i q1_res = blendv8(q1, add16(q1, q1_add), cmplt16(aQ, beta_reg));

    __m256i p0_res = CLIP3_epi16(zero_reg, cliphi_reg, add16(p0, delta));
    __m256i q0_res = CLIP3_epi16(zero_reg, cliphi_reg, sub16(q0, delta));


    p0 = blendv8(p0, p0_res, filter_cond);
    p1 = blendv8(p1, p1_res, filter_cond);
    q0 = blendv8(q0, q0_res, filter_cond);
    q1 = blendv8(q1, q1_res, filter_cond);

    _mm_storeu_si128((__m128i*)&dst[-1*stride], _mm_packus_epi16(_mm256_castsi256_si128(p0), _mm256_extracti128_si256(p0, 1)));
    _mm_storeu_si128((__m128i*)&dst[-2*stride], _mm_packus_epi16(_mm256_castsi256_si128(p1), _mm256_extracti128_si256(p1, 1)));
    _mm_storeu_si128((__m128i*)&dst[ 0*stride], _mm_packus_epi16(_mm256_castsi256_si128(q0), _mm256_extracti128_si256(q0, 1)));
    _mm_storeu_si128((__m128i*)&dst[ 1*stride], _mm_packus_epi16(_mm256_castsi256_si128(q1), _mm256_extracti128_si256(q1, 1)));
}

void deblock_edge_weak_luma_v_avx2(uint8_t *dst, int stride, int alpha, int beta, int indexA, int *bS) {
    __m256i zero_reg = _mm256_setzero_si256();
    __m256i cliphi_reg = _mm256_set1_epi16(0x00FF);


    __m256i p0, p1, p2, q0, q1, q2;
    strided_load_avx_t load = load_strided_avx_16(&dst[-3], stride);
    p2 = _mm256_cvtepu8_epi16(load.c0);
    p1 = _mm256_cvtepu8_epi16(load.c1);
    p0 = _mm256_cvtepu8_epi16(load.c2);
    q0 = _mm256_cvtepu8_epi16(load.c3);
    q1 = _mm256_cvtepu8_epi16(load.c4);
    q2 = _mm256_cvtepu8_epi16(load.c5);



    __m256i tc0 = _mm256_cvtepu8_epi16(_mm_load_si128((__m128i*)get_tc0_table(bS, indexA)));
    __m256i tc0_neg = sub16(zero_reg, tc0);
    __m256i skip = _mm256_cmpgt_epi16(_mm256_set1_epi16(0x0080), tc0);

    __m256i alpha_reg = set1_epi16(alpha);
    __m256i beta_reg  = set1_epi16(beta);

    __m256i p0q0_mask = cmpgt16(alpha_reg, ABS_DIFF_epi16(p0, q0));
    __m256i p1p0_mask = cmpgt16(beta_reg, ABS_DIFF_epi16(p1, p0));
    __m256i q1q0_mask = cmpgt16(beta_reg, ABS_DIFF_epi16(q1, q0));
    __m256i filter_cond = and256(p0q0_mask, and256(p1p0_mask, and256(q1q0_mask, skip)));

    __m256i aP = ABS_DIFF_epi16(p2, p0);
    __m256i aQ = ABS_DIFF_epi16(q2, q0);

    __m256i treshold = add16(tc0, _mm256_add_epi16(THRESH_MASK_epi16_SET1(aP, beta_reg), THRESH_MASK_epi16_SET1(aQ, beta_reg)));
    __m256i treshold_neg = sub16(zero_reg, treshold);


    __m256i delta = mullo16(_mm256_sub_epi16(q0, p0), _mm256_set1_epi16(1 << 2));
    delta = add16(delta, _mm256_sub_epi16(p1, q1));
    delta = add16(delta, _mm256_set1_epi16(4));
    delta = CLIP3_epi16(treshold_neg, treshold, srai16(delta, 3));

    __m256i add_common = srai16(add16(add16(p0, q0), set1_epi16(1)), 1);

    __m256i p1_add = sub16(add_common, slli16(p1, 1));
    p1_add = CLIP3_epi16(tc0_neg, tc0, srai16(add16(p2, p1_add), 1));
    __m256i p1_res = blendv8(p1, add16(p1, p1_add), cmplt16(aP, beta_reg));

    __m256i q1_add = sub16(add_common, slli16(q1, 1));
    q1_add = CLIP3_epi16(tc0_neg, tc0, srai16(add16(q2, q1_add), 1));
    __m256i q1_res = blendv8(q1, add16(q1, q1_add), cmplt16(aQ, beta_reg));

    __m256i p0_res = CLIP3_epi16(zero_reg, cliphi_reg, add16(p0, delta));
    __m256i q0_res = CLIP3_epi16(zero_reg, cliphi_reg, sub16(q0, delta));


    p0 = blendv8(p0, p0_res, filter_cond);
    p1 = blendv8(p1, p1_res, filter_cond);
    q0 = blendv8(q0, q0_res, filter_cond);
    q1 = blendv8(q1, q1_res, filter_cond);

    __m128i p0_store = _mm_packus_epi16(_mm256_castsi256_si128(p0), _mm256_extracti128_si256(p0, 1));
    __m128i p1_store = _mm_packus_epi16(_mm256_castsi256_si128(p1), _mm256_extracti128_si256(p1, 1));
    __m128i q0_store = _mm_packus_epi16(_mm256_castsi256_si128(q0), _mm256_extracti128_si256(q0, 1));
    __m128i q1_store = _mm_packus_epi16(_mm256_castsi256_si128(q1), _mm256_extracti128_si256(q1, 1));

    strided_load_avx_t store = (strided_load_avx_t) {load.c0, p1_store, p0_store, q0_store, q1_store, load.c5, load.c6, load.c7};
    store_strided_avx_16(&dst[-3], stride, store);
}