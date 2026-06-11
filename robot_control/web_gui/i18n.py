"""Minimal i18n for web GUI server messages (SocketIO / HTTP)."""

STRINGS = {
    "login_required":          {"zh": "请先登录",             "en": "Please log in"},
    "permission_denied":       {"zh": "权限不足",             "en": "Permission denied"},
    "cannot_modify_self":      {"zh": "不能修改自己的角色",    "en": "Cannot modify your own role"},
    "cannot_delete_self":      {"zh": "不能删除自己的账号",    "en": "Cannot delete your own account"},
    "unknown_action":          {"zh": "未知操作: {0}",        "en": "Unknown action: {0}"},
    "can_restarted":           {"zh": "CAN 接口已重启",        "en": "CAN interface restarted"},
    "restart_failed":          {"zh": "重启失败: {0}",         "en": "Restart failed: {0}"},
    "username_password_empty": {"zh": "用户名和密码不能为空",   "en": "Username and password required"},
    "username_length":         {"zh": "用户名长度须在 2-32 个字符之间", "en": "Username must be 2-32 characters"},
    "password_mismatch":       {"zh": "两次输入的密码不一致",   "en": "Passwords do not match"},
    "password_length":         {"zh": "密码长度至少 6 位",     "en": "Password must be at least 6 characters"},
    "wrong_credentials":       {"zh": "用户名或密码错误",      "en": "Incorrect username or password"},
    "account_disabled":        {"zh": "账号已被禁用",          "en": "Account disabled"},
    "username_exists":         {"zh": "用户名已存在",          "en": "Username already exists"},
}

DEFAULT_LANG = "zh"


def t(key: str, lang: str = DEFAULT_LANG) -> str:
    """Return the translation for *key* in *lang*, falling back to zh."""
    entry = STRINGS.get(key, {})
    return entry.get(lang, entry.get(DEFAULT_LANG, key))
