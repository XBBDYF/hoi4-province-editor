import os, sys
I18N_DIR = r"D:\hoi4-province-editor\webui\i18n"

def _i18n_locales():
    p = os.path.join(I18N_DIR, "locales.yml")
    try:
        import yaml
        d = yaml.safe_load(open(p, encoding="utf-8")) or {}
        return (d.get("locales") or []), (str(d.get("default") or "zh-CN"))
    except Exception as e:
        print("EXCEPTION:", type(e).__name__, e)
        return [], "zh-CN"

locales, default = _i18n_locales()
print("locales count:", len(locales))
print("default:", default)
print("locales:", locales)
