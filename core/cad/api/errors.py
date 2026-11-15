"""API のエラー。"""


class RpcError(Exception):
    """利用者に見せるエラー（メッセージをそのまま画面に出す）。"""
