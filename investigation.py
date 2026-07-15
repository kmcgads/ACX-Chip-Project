MAIN_COL        = 5
MAIN_H          = 10
MAIN_W          = 15
PIECE_START_COL = 30
PIECE_START_W   = 10
PIECE_END_W     = 5
STRETCH_STEPS   = 25
NECK_START      = MAIN_COL + MAIN_W
PIECE_FINAL_COL = PIECE_START_COL + STRETCH_STEPS  # col=55
NECK_END        = PIECE_FINAL_COL - 1              # col=54

DROP1_ROW   = 55
DROP2_ROW   = 105
MEETING_ROW = (DROP1_ROW + DROP2_ROW) // 2        # row=80

# for i in range(1, STRETCH_STEPS + 1):
#     current_col   = PIECE_START_COL + i
#     current_width = round(PIECE_START_W - (PIECE_START_W - PIECE_END_W) * i / STRETCH_STEPS)
#     print(f"i={i}: current_col={current_col}, current_width={current_width}")
#     print()

for release_col in range(NECK_END, NECK_START - 1, -1):
        bridge_width = release_col - NECK_START
        print (f"Release_col={release_col}")
        print(f"MAIN_H={MAIN_H}, bridge_width={bridge_width}, NECK_START={NECK_START}")

        # if bridge_width > 0:
        #     #Figure out what row is in line 129 and define it
        #     activate(
        #         held_drops(held_rows) + [
        #             Drop(MAIN_H, MAIN_W,       row, MAIN_COL),
        #             Drop(MAIN_H, bridge_width, row, NECK_START),
        #             Drop(MAIN_H, PIECE_END_W,  row, PIECE_FINAL_COL),
        #         ],
        #         debug_label=f"{label} DEACTIVATE col={release_col} bridge={bridge_width}"
        #     )