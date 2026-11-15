from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re

from .go.board import Board, IllegalMove
from .go.coords import Move, Point, from_sgf, other
from .go.game import SUPPORTED_SIZES
from .go.rules import get_rules
from .i18n import tr
from .sgf import SgfError, _Parser, decode_bytes


@dataclass
class TsumegoNode:
    color: str
    move: Move
    comment: str = ""
    accepted: bool = True
    children: list[TsumegoNode] = field(default_factory=list)
    verdict: str = "unknown"


@dataclass
class TsumegoProblem:
    title: str
    size: int
    to_move: str
    board: Board
    root: TsumegoNode
    problem_id: str = ""
    goal: str = ""
    difficulty: str = ""
    source: str = ""
    license: str = ""
    supported: bool = True


DEFAULT_TSUMEGO = (
    "(;GM[1]FF[4]SZ[9]GN[Corner capture]PL[B]AB[ba]AW[aa]"
    "C[Black to capture the corner stone.];B[ab]C[Correct])"
    "(;GM[1]FF[4]SZ[9]GN[Edge capture]PL[W]AW[ba][ab][cb]AB[bb]"
    "C[White to capture the black stone.];W[bc]C[Correct])"
    "(;GM[1]FF[4]SZ[9]GN[Make two eyes]PL[B]AB[ab][bb][cb][db][da]"
    "AW[ac][bc][cc][dc][ec][eb][ea]C[Black to live.]"
    "(;B[ba]C[Correct])(;B[aa]C[Wrong])(;B[ca]C[Wrong]))"
    "(;GM[1]FF[4]SZ[9]GN[Edge chase]PL[B]AB[ba][fa]AW[ca]C[Black to capture.]"
    "(;B[cb];W[da];B[db];W[ea];B[eb]C[Correct])(;B[da];W[cb]C[Wrong]))"
)


def _default_metadata() -> list[tuple[str, str, str]]:
    return [
        (tr("角上吃子"), tr("黑先吃掉角上白子"), tr("入门")),
        (tr("边上吃子"), tr("白先吃掉黑子"), tr("入门")),
        (tr("做出两只眼"), tr("黑先做活"), tr("初级")),
        (tr("沿边追吃"), tr("黑先连续叫吃并吃子"), tr("初级")),
    ]


def localize_default_tsumego(problems: list[TsumegoProblem]) -> None:
    for problem, (title, goal, difficulty) in zip(problems, _default_metadata()):
        problem.title = title
        problem.goal = goal
        problem.difficulty = difficulty
        problem.source = tr("本项目自制示例")
        problem.license = "CC0-1.0"


def default_tsumego() -> list[TsumegoProblem]:
    problems = parse_tsumego(DEFAULT_TSUMEGO)
    localize_default_tsumego(problems)
    return problems


def _verdict(node) -> str:
    comment = (_prop(node, "C") or "").strip()
    wrong = bool(re.fullmatch(
        r"(?:wrong(?: answer)?|incorrect(?: answer)?|failure|错误|不正确)[.!！。]?", comment, re.I))
    correct = bool(re.fullmatch(
        r"(?:correct(?: answer)?|success|正解|正确)[.!！。]?", comment, re.I))
    if wrong:
        return "wrong"
    return "correct" if correct else "unknown"


def _prop(node, name: str) -> str | None:
    prop = node.props.get(name)
    if prop is None:
        return None
    if len(prop.values) != 1:
        raise SgfError(tr("属性 {0} 必须只有一个值", name), prop.line, prop.col)
    return prop.values[0]


def _setup_points(values: list[str], size: int, name: str) -> list[Point]:
    result: list[Point] = []
    for value in values:
        points = value.split(":")
        if len(points) == 1:
            try:
                point = from_sgf(value, size)
            except ValueError as e:
                raise SgfError(str(e)) from None
            if point is None:
                raise SgfError(tr("{0} 属性不能包含停一手", name))
            result.append(point)
            continue
        if len(points) != 2:
            raise SgfError(tr("{0} 属性包含无效区域：{1}", name, value))
        try:
            start = from_sgf(points[0], size)
            end = from_sgf(points[1], size)
        except ValueError as e:
            raise SgfError(str(e)) from None
        if start is None or end is None:
            raise SgfError(tr("{0} 属性包含无效区域：{1}", name, value))
        result.extend(
            (x, y)
            for y in range(min(start[1], end[1]), max(start[1], end[1]) + 1)
            for x in range(min(start[0], end[0]), max(start[0], end[0]) + 1)
        )
    return result


def _append_tree(tree, parent: TsumegoNode, size: int, is_root: bool = False) -> None:
    current = parent
    for index, node in enumerate(tree.nodes):
        if not (is_root and index == 0) and any(name in node.props for name in ("AB", "AW", "AE", "PL")):
            raise SgfError(tr("摆子和指定行棋方属性只支持出现在根节点"), node.line, node.col)
        has_black, has_white = "B" in node.props, "W" in node.props
        if has_black and has_white:
            raise SgfError(tr("同一节点同时包含黑白着手"), node.line, node.col)
        if not (has_black or has_white):
            if not (is_root and index == 0):
                verdict = _verdict(node)
                if verdict != "unknown":
                    if current.verdict != "unknown" and current.verdict != verdict:
                        raise SgfError(tr("答案标记冲突"), node.line, node.col)
                    current.verdict = verdict
                    current.accepted = verdict != "wrong"
            continue
        color = "B" if has_black else "W"
        prop = node.props[color]
        if len(prop.values) != 1:
            raise SgfError(tr("属性 {0} 必须只有一个值", color), prop.line, prop.col)
        try:
            move = from_sgf(prop.values[0].strip(), size)
        except ValueError as e:
            raise SgfError(str(e), prop.line, prop.col) from None
        if move is None:
            raise SgfError(tr("死活题答案不能包含停一手"), prop.line, prop.col)
        comment_prop = node.props.get("C")
        comment = " ".join(comment_prop.values) if comment_prop else ""
        verdict = _verdict(node)
        step = TsumegoNode(color, move, comment, verdict != "wrong", verdict=verdict)
        current.children.append(step)
        current = step
    for child in tree.children:
        _append_tree(child, current, size)


def _validate_turns(node: TsumegoNode, expected: str) -> None:
    for step in node.children:
        if step.color != expected:
            raise SgfError(tr("答案着手颜色不交替，应为{0}方", tr("黑" if expected == "B" else "白")))
        _validate_turns(step, other(expected))


def _validate_moves(node: TsumegoNode, boards: list[Board]) -> None:
    seen = set()
    for step in node.children:
        if step.move in seen:
            raise SgfError(tr("答案分支包含重复着手"))
        seen.add(step.move)
        try:
            board = boards[-1].play(step.color, step.move, get_rules("chinese"))
        except IllegalMove as e:
            raise SgfError(tr("答案棋谱包含非法着法：{0}", e)) from e
        if len(boards) >= 2 and board == boards[-2]:
            raise SgfError(tr("答案棋谱包含立即提劫"))
        _validate_moves(step, boards + [board])


def _content_id(size: int, to_move: str, board: Board, root: TsumegoNode) -> str:
    def tree(node: TsumegoNode):
        return [node.color, node.move, node.verdict,
                sorted((tree(child) for child in node.children), key=repr)]
    data = json.dumps([size, to_move, board.grid, tree(root)], ensure_ascii=True,
                      separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def parse_tsumego(text: str, source_name: str = "") -> list[TsumegoProblem]:
    trees = _Parser(text).collection()
    problems: list[TsumegoProblem] = []
    for index, tree in enumerate(trees, 1):
        root = tree.nodes[0]
        size_text = _prop(root, "SZ")
        try:
            size = int(size_text) if size_text else 19
        except ValueError:
            prop = root.props["SZ"]
            raise SgfError(tr("无效的棋盘尺寸：SZ[{0}]", size_text), prop.line, prop.col) from None
        if size not in SUPPORTED_SIZES:
            prop = root.props.get("SZ")
            raise SgfError(tr("不支持的棋盘尺寸 {0}（仅支持 9/13/19 路）", size),
                           prop.line if prop else root.line, prop.col if prop else root.col)

        grid = [["" for _ in range(size)] for _ in range(size)]
        for color, name in (("B", "AB"), ("W", "AW")):
            prop = root.props.get(name)
            if prop is None:
                continue
            for x, y in _setup_points(prop.values, size, name):
                if grid[y][x]:
                    raise SgfError(tr("摆子位置重复或黑白重叠：{0}", prop.values[0]), prop.line, prop.col)
                grid[y][x] = color
        empty_prop = root.props.get("AE")
        if empty_prop:
            for x, y in _setup_points(empty_prop.values, size, "AE"):
                if grid[y][x]:
                    raise SgfError(tr("摆子位置重复或黑白重叠：{0}", empty_prop.values[0]))

        answer_root = TsumegoNode("", None)
        _append_tree(tree, answer_root, size, is_root=True)
        first = answer_root.children[0] if answer_root.children else None
        to_move = _prop(root, "PL") or (first.color if first else "B")
        if to_move not in ("B", "W"):
            prop = root.props.get("PL")
            raise SgfError(tr("PL 必须是 B 或 W"), prop.line if prop else root.line,
                           prop.col if prop else root.col)
        _validate_turns(answer_root, to_move)
        title = _prop(root, "GN") or (Path(source_name).stem if source_name else tr("题目 {0}", index))
        if len(trees) > 1:
            title = tr("{0} · {1}", title, index)
        board = Board(size, tuple(tuple(row) for row in grid))
        for y, row in enumerate(grid):
            for x, color in enumerate(row):
                if color and not board.group(grid, (x, y))[1]:
                    raise SgfError(tr("题目摆子包含没有气的棋子"))
        _validate_moves(answer_root, [board])
        goal = _prop(root, "C") or ""
        problems.append(TsumegoProblem(
            title, size, to_move, board, answer_root,
            _content_id(size, to_move, board, answer_root),
            goal=goal, difficulty=_prop(root, "RL") or "",
            source=_prop(root, "SO") or "", license=_prop(root, "CP") or "",
            supported=not bool(re.search(r"\bko\b|劫", goal + " " + title, re.I))))
    return problems


def load_tsumego(path: str | Path) -> list[TsumegoProblem]:
    source = Path(path)
    return parse_tsumego(decode_bytes(source.read_bytes()), source.name)
