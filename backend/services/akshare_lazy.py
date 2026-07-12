"""akshare 延迟加载 — 主路径不依赖 akshare 时模块仍可 import。"""


def akshare():
    import akshare as ak

    return ak
