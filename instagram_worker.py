#!/usr/bin/env python3
"""Worker curto do yt-dlp; cookies/variáveis do Railway são herdados normalmente."""
import json
import sys
import instagram_import


def main():
    with open(sys.argv[1], "r", encoding="utf-8") as f:
        p = json.load(f)
    try:
        caminho, nome = instagram_import.baixar_video_instagram(
            url=str(p.get("url") or ""),
            pasta_destino=str(p.get("pasta_destino") or ""),
            identificador=str(p.get("identificador") or ""),
            limite_mb=int(p.get("limite_mb") or 200),
        )
        print(json.dumps({"ok": True, "path": caminho, "nome": nome}, ensure_ascii=False))
    except instagram_import.InstagramImportError as exc:
        # Erro esperado (URL inválida, cookies, bloqueio) continua amigável na interface.
        print(json.dumps({"ok": False, "esperado": True, "erro": str(exc)}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"ok": False, "erro": str(exc)}, ensure_ascii=False))
        raise SystemExit(1)
