"""量測層：把 ffmpeg 的輸出變成數字。這個模組不做任何決定。

判斷（該用哪條濾鏡、要不要處理）一律在 SKILL.md 交給宿主 LLM，見 spec ADR-4。
"""

_S_PREFIX = "lavfi.r128.S="
_PTS_MARKER = "pts_time:"


def parse_short_term(stdout):
    """ametadata 的 stdout -> [(pts_time, short_term_LUFS), ...]

    格式為兩行一組：先 `frame:N    pts:...    pts_time:T`，再 `lavfi.r128.S=<值>`。
    只有看到 S 行才收一筆，所以缺 pts_time 的孤兒 S 行會沿用上一個時間。
    """
    out = []
    t = 0.0
    for line in stdout.splitlines():
        if _PTS_MARKER in line:
            try:
                t = float(line.split(_PTS_MARKER, 1)[1].split()[0])
            except (IndexError, ValueError):
                continue
        elif line.startswith(_S_PREFIX):
            try:
                out.append((t, float(line[len(_S_PREFIX):].strip())))
            except ValueError:
                continue
    return out
