from __future__ import annotations

from typing import Protocol, TypeVar, cast

_T = TypeVar("_T", covariant=True)


# Dear PyGui の context item を型検査へ伝える。
class DpgContext(Protocol[_T]):
    """Dear PyGui の context item を型検査へ伝えるための最小 protocol。"""

    def __enter__(self) -> _T: ...

    def __exit__(self, exc_type: object, exc: object, tb: object) -> object: ...


# dearpygui の framework 適合処理を集約する。
class DpgSupport:
    """dearpygui の runtime 契約を型 checker へ橋渡しする helper。

    dearpygui の container 生成関数は runtime では context manager として振る舞うが、
    型 stub 上は item id を返すだけのため、`with` 構文が型検査を通らない。
    その差分をここで 1 か所に閉じ込め、描画層と theme 層の双方から共有する。
    """

    # runtime context manager を型付けする。
    @staticmethod
    def context(item: _T) -> DpgContext[_T]:
        """Dear PyGui の runtime context manager を型 checker 向けに明示する。"""

        return cast(DpgContext[_T], item)
