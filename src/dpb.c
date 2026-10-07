//
// Created by gmathix on 4/21/26.
//




/*
 * This is quite a big file so here's a guide to help you find your way :
 * - l.34-107  : ref picture list sorting utils
 * - l.111-220 : ref picture numbers management
 * - l.224-320 : internal picture slots management
 * - l.326-441 : MMCOs
 * - l.446-779 : ref picture list management
 * - l.784-end : rest of the public API
 */



#include "dpb.h"
#include "slice.h"

#include "tests/profiler.h"
#include "util/expgolomb.h"
#include "util/formulas.h"
#include "util/sliceutil.h"


int picture_to_find = -1;



/** some java-style sorting abstraction for l0 and l1 initializations because i miss java :( **/

typedef int (*PictureField) (const Picture *pic);
static int returnPicNum(const Picture *pic)   { return pic->pic_num; }
static int returnLTPicNum(const Picture *pic) { return pic->long_term_frame_idx; }
static int returnPoc(const Picture *pic)      { return pic->poc; }

typedef bool (*RefTypeCriteria) (const Picture *pic);
static bool shortTermCriteria(const Picture *pic) { return pic->dpb_status == SHORT_TERM_REF; }
static bool longTermCriteria(const Picture *pic)  { return pic->dpb_status == LONG_TERM_REF; }

typedef bool (*ReferenceComparator) (int field, int ref);
static bool greaterThan(int field, int ref)    { return field > ref; }
static bool lowerThan(int field, int ref)      { return field < ref; }
static bool greaterOrEqual(int field, int ref) { return field >= ref; }
static bool lowerOrEqual(int field, int ref)   { return field <= ref; }
static bool equalTo(int field, int ref)        { return field == ref; }
static bool dontGiveAShit(int field, int ref)  { return true || field || ref; } // just to suppress to compiler warning


/* returns how many pictures were added to the dest list */
static int sortToRefList(DPB *dpb, bool descending, int list, int *idx,
    PictureField fieldGetter, RefTypeCriteria criteria, ReferenceComparator comparator, Picture *refPic) {

    int nbAdded = 0;

    int bestIdx = 0;
    int best = descending ? INT32_MIN : INT32_MAX;
    int prevBest = descending ? INT32_MAX : INT32_MIN;

    for (int i = 0; i < MAX_DPB_SIZE; i++) {
        for (int j = 0; j < MAX_DPB_SIZE; j++) {
            Picture *pic = dpb->slots[j];
            if (pic == NULL) continue;
            int field = (fieldGetter)(pic);
            int fieldRef = (fieldGetter)(refPic);
            bool compareResult = descending ?
                  field < prevBest && field > best
                : field > prevBest && field < best;
            bool criteriaResult = (criteria)(pic);
            bool refCompareResult = (comparator)(field, fieldRef);
            if (criteriaResult && compareResult && refCompareResult) {
                best = field;
                bestIdx = j;
            }
        }
        if ((descending && best > INT32_MIN) || (!descending && best < INT32_MAX)) {
            Picture *pic = dpb->slots[bestIdx];
            if (!pic->in_list[list]) {
                pic->lowest_list_index[list] = *idx;
                pic->in_list[list] = true;
            }

            dpb->lists[list][1+(*idx)++] = pic;

            nbAdded++;
            prevBest = best;
            best = descending ? INT32_MIN : INT32_MAX;
        } else break;
    }

    return nbAdded;
}

static Picture *findRefPic(DPB *dpb, PictureField fieldGetter, int fieldValue) {
    for (int i = 0; i < dpb->size; i++) {
        Picture *pic = dpb->slots[i];
        if (pic == NULL) continue;
        if ((fieldGetter)(pic) == fieldValue && pic->dpb_status != UNUSED_REF) {
            return pic;
        }
    }
    return NULL;
}



/** ==================== REFERENCE PICTURE NUMBERS MANAGEMENT ==================== **/

static int picNum(Picture **lX, int idx, int maxPicNum) {
    if (lX[1+idx] != NULL && lX[1+idx]->dpb_status == SHORT_TERM_REF)
        return lX[1+idx]->pic_num;
    return maxPicNum;
}

static int ltPicNum(Picture **lX, int idx, int maxLtIdx) {
    if (lX[1+idx] != NULL && lX[1+idx]->dpb_status == LONG_TERM_REF)
        return lX[1+idx]->long_term_frame_idx;
    return 2 * (maxLtIdx + 1);
}

void derive_poc(DPB *dpb, Picture *pic) {
    if (dpb->maxPocLsb == -1) {
        dpb->maxPocLsb = 1 << (dpb->ctx->ps->sps->log2_max_poc_lsb_minus4 + 4);
    }
    if (pic->sh->sps->poc_type == 0) {
        /* most used, provides the most flexibility for complex GOP
         * and handles reordering and gaps in frame numbers gracefully
         */

        if (pic->sh->idr_pic_flag) {
            dpb->prevPocLsb = 0;
            dpb->prevPocMsb = 0;
        }
        int curr_poc_lsb = pic->sh->poc_lsb;
        int pocMsb;
        if (curr_poc_lsb < dpb->prevPocLsb && dpb->prevPocLsb - curr_poc_lsb >= dpb->maxPocLsb / 2) {
            pocMsb = dpb->prevPocMsb + dpb->maxPocLsb;
        } else if (curr_poc_lsb > dpb->prevPocLsb && curr_poc_lsb - dpb->prevPocLsb > dpb->maxPocLsb / 2) {
            pocMsb = dpb->prevPocMsb - dpb->maxPocLsb;
        } else {
            pocMsb = dpb->prevPocMsb;
        }
        pic->top_field_order_cnt = pocMsb + curr_poc_lsb;
        pic->poc = pic->top_field_order_cnt;

        dpb->prevPocLsb = curr_poc_lsb;
        dpb->prevPocMsb = pocMsb;

    } else if (pic->sh->sps->poc_type == 1) {
        /* virtually unused, because it's dense and very fragile
         * most encoders found that the bits saved weren't worth the complexity
         * in fact, many hardware decoders have had bugs specifically in their type 1 implementation,
         * just because there is so little content out there to test with
         */


        printf("poc type 1 not implemented, i refuse to implement the cursed POC mode\n");
        exit(67);

    } else if (pic->sh->sps->poc_type == 2) {
        /* commonly used (specific use cases)
         * it's extremely efficient because it requires almost no extra bits in the slice header,
         * but it cannot support B frames
         * so if the stream is strictly I-P-P-P, type 2 is the way to go
         */

        int prevFrameNum = dpb->prevPic == NULL ? 0 : dpb->prevPic->frame_num;
        int prevFrameNumOffset;
        if (!pic->sh->idr_pic_flag) {
            prevFrameNumOffset = dpb->mmco_5_prev_occured ? 0 : dpb->prevPic->frame_num_offset;
        }
        if (pic->sh->idr_pic_flag) {
            pic->frame_num_offset = 0;
        } else if (prevFrameNum > pic->frame_num) {
            pic->frame_num_offset = prevFrameNumOffset + (1 << (pic->sh->sps->log2_max_frame_num_minus4 + 4));
        } else {
            pic->frame_num_offset = prevFrameNumOffset;
        }

        int tmpPoc;
        if (pic->sh->idr_pic_flag) {
            tmpPoc = 0;
        } else if (pic->nal_ref_idc == 0) {
            tmpPoc = 2 * (pic->frame_num_offset + pic->frame_num) - 1;
        } else {
            tmpPoc = 2 * (pic->frame_num_offset + pic->frame_num);
        }

        if (!pic->sh->field_pic_flag) {
            pic->top_field_order_cnt    = tmpPoc;
            pic->bottom_field_order_cnt = tmpPoc;
            pic->poc                    = tmpPoc;
        } else if (pic->sh->bottom_field_flag) {
            pic->bottom_field_order_cnt = tmpPoc;
        } else {
            pic->top_field_order_cnt    = tmpPoc;
        }
    }
}

static void decode_pic_nums(DPB *dpb, int frame_num) {
    for (int i = 0; i < dpb->size; i++) {
        Picture *pic = dpb->slots[i];
        if (pic != NULL) {
            if (pic->dpb_status == SHORT_TERM_REF) {
                if (pic->frame_num > frame_num)
                    pic->frame_num_wrap = pic->frame_num - dpb->ctx->maxFrameNum;
                else
                    pic->frame_num_wrap = pic->frame_num;
                pic->pic_num = pic->frame_num_wrap;
            } else if (pic->dpb_status == LONG_TERM_REF) {
                pic->pic_num = pic->long_term_frame_idx;
            }
        }
    }
}



/** ==================== INTERNAL PICTURE SLOTS MANAGEMENT ==================== **/

static void dpb_empty_ref_lists(DPB *dpb) {
    for (int i = 0; i < dpb->size; i++) {
        Picture *pic = dpb->slots[i];
        if (pic) {
            pic->in_list[L0] = pic->in_list[L1] = false;
            pic->lowest_list_index[L0] = pic->lowest_list_index[L1] = -1;
        }
    }
    for (int i = 0; i < dpb->size + 2; i++) {
        dpb->lists[L0][i] = NULL;
        dpb->lists[L1][i] = NULL;
    }
    dpb->effective_ref_idx_l0_active = 0;
    dpb->effective_ref_idx_l1_active = 0;
}

static void output_pic(int idx, DPB *dpb) {
    Picture *pic = dpb->slots[idx];

    if (dpb->pictures_dumped == picture_to_find) {
        // stderr to see it better
        fprintf(stderr, "Picture %d : POC:%d frame_num:%d\n", picture_to_find, pic->poc, pic->frame_num);
    }

    if (!pic->non_existing)
        dump_picture(pic, dpb->ctx);
    pic->is_output = true;
    free(pic->sh);

    pic_pool_getback(pic, dpb->ctx->pool);
    dpb->slots[idx] = NULL;
    dpb->fullness--;
    dpb->pictures_dumped++;
}

static int output_oldest_pic(DPB *dpb) {
    int min_poc = INT32_MAX;
    int min_idx = -1;
    for (int i = 0; i < dpb->size; i++) {
        Picture *pic = dpb->slots[i];
        if (pic != NULL && pic->poc < min_poc && !pic->is_output) {

            min_poc = pic->poc;
            min_idx = i;
        }
    }
    if (min_idx != -1) {
        output_pic(min_idx, dpb);
    }

    return min_idx;
}

static int bump(DPB *dpb) {
    for (int i = 0; i < dpb->size; i++) {
        if (dpb->slots[i] == NULL) {
            return i;
        }
    }
    return output_oldest_pic(dpb);
}

static int sliding_window_marking(SliceHeader *sh, DPB *dpb) {
    int numShortTerm = 0;
    int numLongTerm = 0;
    for (int i = 0; i < dpb->size; i++) {
        if (dpb->slots[i] != NULL && dpb->slots[i]->dpb_status == SHORT_TERM_REF) {
            numShortTerm++;
        } else if (dpb->slots[i] != NULL && dpb->slots[i]->dpb_status == LONG_TERM_REF) {
            numLongTerm++;
        }
    }
    if (numShortTerm + numLongTerm == _max(sh->sps->max_num_ref_frames, 1)) {
        int minFrameNumWrap = INT32_MAX;
        int minIdx = 0;
        for (int i = 0; i < dpb->size; i++) {
            Picture *pic = dpb->slots[i];
            if (pic != NULL && pic->dpb_status == SHORT_TERM_REF && pic->frame_num_wrap < minFrameNumWrap) {
                minFrameNumWrap = pic->frame_num_wrap;
                minIdx = i;
            }
        }
        dpb->slots[minIdx]->dpb_status = UNUSED_REF;
        return minIdx;
    }
    return -1;
}

static void pad_list_with_empty(DPB *dpb, Picture **list) {
    for (int i = 0; i < dpb->size; i++) {
        if (list[i] == NULL) {
            list[i] = &EMPTY_PICTURE;
        }
    }
}





/** ==================== MMCOs (MEMORY CONTROL MANAGEMENT OPERATIONS) ==================== **/

static void mark_st_pic_unused(DPB *dpb, int picNum) {
    Picture *pic = findRefPic(dpb, returnPicNum, picNum);
    if (pic) {
        pic->dpb_status = UNUSED_REF;
    } else {
        printf("warning: MMCO 1 tried to modify a non-existing picture, ignoring\n");
    }
}
static void mark_lt_pic_unused(DPB *dpb, int ltPicNum) {
    Picture *pic = findRefPic(dpb, returnLTPicNum, ltPicNum);
    if (pic) {
        pic->dpb_status = UNUSED_REF;
    } else {
        printf("warning: MMCO 2 tried to modify a non-existing picture, ignoring\n");
    }
}
static void assign_lt_idx_to_st_pic(DPB *dpb, int picNum, int lt_frame_idx) {
    Picture *pic = findRefPic(dpb, returnPicNum, picNum);
    if (pic) {
        for (int i = 0; i < dpb->size; i++) {
            Picture *pic = dpb->slots[i];
            if (pic && pic->dpb_status == LONG_TERM_REF && pic->long_term_frame_idx == lt_frame_idx) {
                pic->dpb_status = UNUSED_REF;
                pic->long_term_frame_idx = -1;
            }
        }
        pic->dpb_status = LONG_TERM_REF;
        pic->long_term_frame_idx = lt_frame_idx;
    } else {
        printf("warning: MMCO 3 tried to modify a non-existing picture, ignoring\n");
    }
}
static void decode_max_lt_frame_idx(DPB *dpb, int max_lt_frame_idx) {
    dpb->ctx->maxLongTermFrameIdx = max_lt_frame_idx;
    for (int i = 0; i < dpb->size; i++) {
        Picture *pic = dpb->slots[i];
        if (pic != NULL && pic->dpb_status == LONG_TERM_REF && pic->long_term_frame_idx > max_lt_frame_idx) {
            pic->dpb_status = UNUSED_REF;
        }
    }
}
static void mark_all_unused(DPB *dpb) {
    dpb->ctx->maxLongTermFrameIdx = 0;
    for (int i = 0; i < dpb->size; i++) {
        if (dpb->slots[i] != NULL) {
            dpb->slots[i]->dpb_status = UNUSED_REF;
        }
    }
}
static void mark_curr_pic_lt(DPB *dpb, int lt_frame_idx) {
    for (int i = 0; i < dpb->size; i++) {
        Picture *pic = dpb->slots[i];
        if (pic && pic->dpb_status == LONG_TERM_REF && pic->long_term_frame_idx == lt_frame_idx) {
            pic->dpb_status = UNUSED_REF;
            pic->long_term_frame_idx = -1;
        }
    }
    dpb->ctx->curr_pic->dpb_status = LONG_TERM_REF;
    dpb->ctx->curr_pic->long_term_frame_idx = lt_frame_idx;
}


static void process_mmcos(Picture *pic, const Undo264Context *ctx) {
    if (pic->sh->adaptive_ref_pic_marking_mode_flag) {

        /* use a new bitreader to read back at the MMCO position in the bitstream */
        BitReader mmco_br = make_br(ctx->br->data, ctx->br->size);

        mmco_br.byte_pos = pic->sh->mmco_position_bits / 8;
        mmco_br.cache_pos  = pic->sh->mmco_position_bits % 8;
        bitreader_refill(&mmco_br);

        uint32_t mmco = 0;
        do {
            mmco = read_ue(&mmco_br);

            if (mmco == 1) {
                uint32_t diff_pic_nums = read_ue(&mmco_br) + 1;
                mark_st_pic_unused(ctx->dpb, pic->frame_num - diff_pic_nums);
            }
            if (mmco == 2) {
                uint32_t lt_pic_num = read_ue(&mmco_br);
                mark_lt_pic_unused(ctx->dpb, lt_pic_num);
            }
            if (mmco == 3) {
                uint32_t diff_pic_nums = read_ue(&mmco_br) + 1;
                uint32_t lt_frame_idx = read_ue(&mmco_br);
                assign_lt_idx_to_st_pic(ctx->dpb, pic->frame_num - diff_pic_nums, lt_frame_idx);
            }
            if (mmco == 4) {
                uint32_t max_lt_frame_idx = read_ue(&mmco_br) - 1;
                decode_max_lt_frame_idx(ctx->dpb, max_lt_frame_idx);
            }
            if (mmco == 5) {
                mark_all_unused(ctx->dpb);
                ctx->dpb->mmco_5_prev_occured = true;
                pic->frame_num = 0;
                for (int i = 0; i < ctx->dpb->size; i++) {
                    output_oldest_pic(ctx->dpb);
                }
                for (int i = 0; i < ctx->dpb->size; i++) {
                    if (ctx->dpb->slots[i] && ctx->dpb->slots[i]->non_existing) {
                        output_pic(i, ctx->dpb);
                    }
                }
                ctx->curr_pic->poc = 0;
            }
            if (mmco == 6) {
                uint32_t lt_frame_idx = read_ue(&mmco_br);
                mark_curr_pic_lt(ctx->dpb, lt_frame_idx);
            }
        } while (mmco != 0);
    }
}




/** ==================== REFERENCE PICTURE LISTS MANAGEMENT ==================== **/

void init_ref_pic_lists(DPB *dpb, SliceHeader *sh) {
    dpb_empty_ref_lists(dpb);

    decode_pic_nums(dpb, sh->frame_num);

    Picture *curr_pic = dpb->ctx->curr_pic;

    if (IS_P_SLICE(sh->slice_type) || IS_SP_SLICE(sh->slice_type)) {
        /* short-term ref frames first, sorted by descending PicNum
         * long-term ref frames second, sorted by ascending PicNum
         */

        int idx = 0;
        int nbAdded = 0;
        nbAdded += sortToRefList(dpb, true, L0, &idx,
            returnPicNum,
            shortTermCriteria,
            dontGiveAShit,
            curr_pic);
        nbAdded += sortToRefList(dpb, false, L0,
            &idx, returnPicNum,
            longTermCriteria,
            dontGiveAShit,
            curr_pic);

        dpb->effective_ref_idx_l0_active += nbAdded;

    } else if (IS_B_SLICE(sh->slice_type)) {
        /* l0:
         *   1. short term pics with POC < CurrentPOC, sorted by descending POC
         *      short term pics with POC >= CurrentPoc, sorted by ascending POC
         *   2. long term pics sorted by ascending LongTermPicNum
         *
         * l1:
         *   1. short term pics with POC > CurrentPOC, sorted by ascending POC
         *      short term pics with POC <= CurrentPOC, sorted by descending POC
         *   2. long term pics sorted by ascending LongTermPicNum
         *   3. if l1 == l0, switch l1[0] and l1[1]
         */

        int idx = 0;

        int nbAdded = 0;
        nbAdded += sortToRefList(dpb, true,  L0, &idx,
            returnPoc,
            shortTermCriteria,
            lowerThan,
            curr_pic);
        nbAdded += sortToRefList(dpb, false, L0, &idx,
            returnPoc,
            shortTermCriteria,
            greaterOrEqual,
            curr_pic);
        nbAdded += sortToRefList(dpb, false, L0, &idx,
            returnLTPicNum,
            longTermCriteria,
            dontGiveAShit,
            curr_pic);

        dpb->effective_ref_idx_l0_active += nbAdded;


        idx = 0;
        nbAdded = 0;
        nbAdded += sortToRefList(dpb, false, L1, &idx,
            returnPoc,
            shortTermCriteria,
            greaterThan,
            curr_pic);
        nbAdded += sortToRefList(dpb, true, L1, &idx,
            returnPoc,
            shortTermCriteria,
            lowerOrEqual,
            curr_pic);
        nbAdded += sortToRefList(dpb, false, L1, &idx,
            returnLTPicNum,
            longTermCriteria,
            dontGiveAShit,
            curr_pic);

        dpb->effective_ref_idx_l1_active += nbAdded;


        /* check if l0 == l1 */
        bool equal = true;
        for (int i = 0; i < dpb->size; i++) {
            if (dpb->lists[L0][1+i] != dpb->lists[L1][1+i]) {
                equal = false;
                break;
            }
        }
        if (equal && dpb->effective_ref_idx_l1_active > 1) {
            Picture *tmp = dpb->lists[L1][1+0];
            dpb->lists[L1][1+0] = dpb->lists[L1][1+1];
            dpb->lists[L1][1+1] = tmp;
        }
    }

    pad_list_with_empty(dpb, dpb->lists[L0]);
    pad_list_with_empty(dpb, dpb->lists[L1]);
}

// puts a short-term ref picture at position refIdxLX (advancing 0..num_ref_idx_active-1)
static void ref_pic_list_modif_st(Slice *slice, bool is_l0, int *refIdxLX, int modif_idc, int abs_diff, int maxFrameNum, const Undo264Context *ctx) {
    DPB *dpb = ctx->dpb;

    bool rplm_occured = is_l0 ? slice->rplm_occured_l0 : slice->rplm_occured_l1;
    int prevPicNumLXPred = is_l0 ? slice->picNumL0Pred : slice->picNumL1Pred;
    int num_ref_frames_active = is_l0 ? slice->sh->num_ref_idx_l0_active_minus1+1: slice->sh->num_ref_idx_l1_active_minus1+1;

    int picNumLXPred = rplm_occured
        ? prevPicNumLXPred
        : ctx->curr_pic->pic_num;
    Picture **lX = is_l0 ? dpb->lists[L0] : dpb->lists[L1];

    int picNumLXNoWrap = 0;
    if (modif_idc == 0) {
        picNumLXNoWrap = (picNumLXPred - abs_diff) < 0
            ? picNumLXPred - abs_diff + maxFrameNum
            : picNumLXPred - abs_diff;
    } else {
        picNumLXNoWrap = (picNumLXPred + abs_diff) >= maxFrameNum
            ? picNumLXPred + abs_diff - maxFrameNum
            : picNumLXPred + abs_diff;
    }

    if (is_l0) {
        slice->rplm_occured_l0 = true;
        slice->picNumL0Pred = picNumLXNoWrap;
    } else {
        slice->rplm_occured_l1 = true;
        slice->picNumL1Pred = picNumLXNoWrap;
    }

    int picNumLX = picNumLXNoWrap > ctx->curr_pic->pic_num
        ? picNumLXNoWrap - maxFrameNum
        : picNumLXNoWrap;

    Picture *refpic = findRefPic(dpb, returnPicNum, picNumLX);
    for (int cIdx = num_ref_frames_active; cIdx > *refIdxLX; cIdx--) {
        lX[1+cIdx] = lX[1+cIdx-1];
    }
    lX[1+(*refIdxLX)++] = refpic;
    int nIdx = *refIdxLX;
    for (int cIdx = *refIdxLX; cIdx <= num_ref_frames_active; cIdx++) {
        if (picNum(lX, cIdx, maxFrameNum) != picNumLX) {
            lX[1+nIdx] = lX[1+cIdx];
            nIdx++;
        }
    }
}

// puts a long-term ref picture at position refIdxLX (advancing 0..num_ref_idx_active-1)
static void ref_pic_list_modif_lt(Slice *slice, bool is_l0,  int *refIdxLX, int modif_idc, int lt_pic_num, int *maxLtIdx, const Undo264Context *ctx) {
    DPB *dpb = ctx->dpb;

    int picNumLXPred = ctx->curr_pic->pic_num;
    int num_ref_idx_lX_active = is_l0 ? slice->sh->num_ref_idx_l0_active_minus1+1 : slice->sh->num_ref_idx_l1_active_minus1;
    Picture **lX = is_l0 ? dpb->lists[L0] : dpb->lists[L1];

    Picture *refpic = findRefPic(dpb, returnLTPicNum, lt_pic_num);
    for (int cIdx = num_ref_idx_lX_active; cIdx > *refIdxLX; cIdx--) {
        lX[1+cIdx] = lX[1+cIdx-1];
    }
    lX[1+(*refIdxLX)++] = refpic;
    int nIdx = *refIdxLX;
    for (int cIdx = *refIdxLX; cIdx <= num_ref_idx_lX_active; cIdx++) {
        if (ltPicNum(lX, cIdx, *maxLtIdx) != lt_pic_num) {
            lX[1+nIdx++] = lX[1+cIdx];
        }
    }
}

void ref_pic_list_modification(uint8_t type, Slice *slice, int maxFrameNum, int *maxLtIdx, const Undo264Context *ctx) {
    BitReader *br = ctx->br;

    int refIdxL0 = 0;
    if (type%5 != 2 && type%5 != 4) {
        #ifdef SLICES_LOG
            printf("  * l0 modifications\n");
        #endif
        int l0_modif_flag = read_u(br, 1);
        if (l0_modif_flag) {
            uint32_t modif_idc = 0;
            do {
                modif_idc = read_ue(br);
                #ifdef SLICES_LOG
                    printf("   modif_idc:%d\n", modif_idc);
                #endif
                if (modif_idc == 0 || modif_idc == 1) {
                    uint32_t abs_diff = read_ue(br) + 1;
                    ref_pic_list_modif_st(slice, true, &refIdxL0, modif_idc, abs_diff, maxFrameNum, ctx);
                } else if (modif_idc == 2) {
                    uint32_t lt_pic_num = read_ue(br);
                    ref_pic_list_modif_lt(slice, true, &refIdxL0, modif_idc, lt_pic_num, maxLtIdx, ctx);
                }
            } while (modif_idc != 3);
        } else {
            #ifdef SLICES_LOG
                printf("    no modification\n");
            #endif
        }
    }

    int refIdxL1 = 0;
    if (type%5 == 1) {
        #ifdef SLICES_LOG
            printf("  * l1 modifications\n");
        #endif
        int l1_modif_flag = read_u(br, 1);
        if (l1_modif_flag) {
            uint32_t modif_idc = 0;
            do {
                modif_idc = read_ue(br);
                if (modif_idc == 0 || modif_idc == 1) {
                    uint32_t abs_diff = read_ue(br) + 1;
                    ref_pic_list_modif_st(slice, false, &refIdxL1, modif_idc, abs_diff, maxFrameNum, ctx);
                } else if (modif_idc == 2) {
                    uint32_t lt_pic_num = read_ue(br);
                    ref_pic_list_modif_lt(slice, false, &refIdxL1, modif_idc, lt_pic_num, maxLtIdx, ctx);
                }
            } while (modif_idc != 3);
        } else {
            #ifdef SLICES_LOG
                printf("    no modifications\n");
            #endif
        }
    }
}

/* 7.3.3.3 */
void dec_ref_pic_marking(DPB *dpb, Slice *slice, BitReader *br) {

    SliceHeader *sh = slice->sh;
    Picture *currPic = dpb->ctx->curr_pic;

    // gaps in frame_num
    if (dpb->prevPic
        && sh->frame_num != dpb->prevPic->frame_num
        && sh->frame_num != (dpb->prevPic->frame_num + 1) % dpb->ctx->maxFrameNum
        && !sh->idr_pic_flag) {

        if (!sh->sps->gaps_in_frame_num_allowed_flag) {
            printf("warning: disallowed gap in frame_num detected\n");
        } else {
            printf("resolving gap in frame_num\n");
        }

        int curr_frame_num = dpb->prevPic->frame_num;
        do {
            curr_frame_num = (curr_frame_num + 1) % dpb->ctx->maxFrameNum;

            decode_pic_nums(dpb, curr_frame_num);
            int idx = sliding_window_marking(sh, dpb);
            if (idx != -1) {
                // picture was marked unused
                Picture *pic = dpb->slots[idx];
                if (pic->non_existing) {
                    output_pic(idx, dpb);
                } else {
                    idx = -1;
                }
            }


            Picture *nonExistingPic        = pic_pool_get(dpb->ctx->pool);
            nonExistingPic->frame_num      = curr_frame_num;
            nonExistingPic->pic_num        = curr_frame_num;
            nonExistingPic->frame_num_wrap = curr_frame_num;
            nonExistingPic->non_existing   = true;
            nonExistingPic->dpb_status     = SHORT_TERM_REF;
            nonExistingPic->is_output      = false;
            nonExistingPic->nal_ref_idc    = currPic->nal_ref_idc;
            nonExistingPic->long_term_frame_idx = -1;

            nonExistingPic->sh = calloc(1, sizeof(SliceHeader));
            memcpy(nonExistingPic->sh, currPic->sh, sizeof(SliceHeader));


            derive_poc(dpb, nonExistingPic);


            if (idx == -1) {
                idx = bump(dpb);
            }

            dpb->slots[idx] = nonExistingPic;
            dpb->prevPic = nonExistingPic;
            dpb->fullness++;
        } while (curr_frame_num != sh->frame_num &&
            ((curr_frame_num + 1) % dpb->ctx->maxFrameNum) != sh->frame_num);
    }

    if (slice->sh->idr_pic_flag) {

        slice->sh->no_output_of_prior_pics_flag  = read_u(br, 1);
        slice->sh->long_term_reference_flag      = read_u(br, 1);

    } else {
        slice->sh->adaptive_ref_pic_marking_mode_flag = read_u(br, 1);
        if (slice->sh->adaptive_ref_pic_marking_mode_flag) {

            /* passive parsing. actual parsing and operations will be done when picture is stored */
            slice->sh->mmco_position_bits = br->byte_pos*8 + br->cache_pos;

            uint32_t mmco = 0;
            do {
                mmco = read_ue(br);

                #ifdef SLICES_LOG
                    printf("mmco:%d\n", mmco);
                #endif
                if (mmco == 1 || mmco == 3) {
                    uint32_t diff_pic_nums = read_ue(br) + 1;
                }
                if (mmco == 2) {
                    uint32_t lt_pic_num = read_ue(br);
                }
                if (mmco == 3 || mmco == 6) {
                    uint32_t lt_frame_idx = read_ue(br);
                }
                if (mmco == 4) {
                    uint32_t max_lt_frame_idx = read_ue(br) - 1;
                }
            } while (mmco != 0);
        } else {
            #ifdef SLICES_LOG
                printf("no MMCOs\n");
            #endif
        }
    }
}




/** ==================== PRIMARY PUBLIC API ==================== **/

void dpb_flush(DPB *dpb) {
	for (int i = 0; i < dpb->size; i++) {
		output_oldest_pic(dpb);
	}
}

void dpb_free(DPB *dpb) {
    for (int i = 0; i < dpb->size; i++) {
        if (dpb->slots[i] != NULL) {
            pic_pool_getback(dpb->slots[i], dpb->ctx->pool);
        }
    }
    free(dpb);
}


void store_picture(DPB *dpb, Picture *pic) {
    // derive_poc(dpb, pic);
    decode_pic_nums(dpb, pic->sh->frame_num);

    dpb->mmco_5_prev_occured = false;
    if (pic->sh->idr_pic_flag) {
        for (int i = 0; i < dpb->size; i++) {
            output_oldest_pic(dpb);
        }
        for (int i = 0; i < dpb->size; i++) {
            if (dpb->slots[i] && dpb->slots[i]->non_existing) {
                output_pic(i, dpb);
            }
        }
        dpb->fullness = 0;
        if (pic->sh->long_term_reference_flag) {
            pic->dpb_status = LONG_TERM_REF;
            pic->long_term_frame_idx = 0;
            dpb->ctx->maxLongTermFrameIdx = 0;
        } else {
            pic->dpb_status = SHORT_TERM_REF;
        }
    } else {
        if (!pic->sh->adaptive_ref_pic_marking_mode_flag) {
            if (pic->nal_ref_idc != 0) {
                int idx = sliding_window_marking(pic->sh, dpb);
            }
        } else {
            // MMCOs
            process_mmcos(pic, dpb->ctx);
        }
    }


    if (pic->dpb_status != LONG_TERM_REF && pic->nal_ref_idc != 0) {
        pic->dpb_status = SHORT_TERM_REF;
        pic->long_term_frame_idx = -1;
    }


    pic->dpb_pic_id = dpb->curr_pic_dpb_id;
    while (1) {
        dpb->curr_pic_dpb_id = (dpb->curr_pic_dpb_id + 1) % (MAX_DPB_SIZE+2); // 0 reserved for EMPTY_PICTURE
        if (dpb->curr_pic_dpb_id == 0) dpb->curr_pic_dpb_id = 1;

        // check that no other picture has this id (could happen with long-term ref frames)
        int match = 0;
        for (int i = 0; i < dpb->size; i++) {
            if (dpb->slots[i] && dpb->slots[i]->dpb_pic_id == pic->dpb_pic_id && dpb->slots[i] != pic) {
                pic->dpb_pic_id = dpb->curr_pic_dpb_id;
                match = 1;
            }
        }
        if (!match) break;
    }



    int index = bump(dpb);

    dpb->slots[index] = pic;
    dpb->prevPic = pic;
    dpb->fullness++;
}