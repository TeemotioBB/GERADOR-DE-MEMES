# Gerador de Posts — Adulto Sofrido

Sisteminha que monta seus vídeos no template de tweet (fundo branco 9:16, com
foto de perfil, nome, @ e legenda). Você envia o vídeo e a legenda, e ele
devolve o vídeo já editado, pronto pra postar.

Funciona de duas formas: por uma **página web** (recomendado) ou direto pelo
**terminal**.

---

## O que você precisa instalar (uma vez só)

1. **Python 3** — https://www.python.org/downloads/ (marque "Add to PATH" no
   instalador, se for Windows).
2. **FFmpeg** — o motor que edita o vídeo:
   - Windows: https://www.gyan.dev/ffmpeg/builds/ (baixe o "release essentials",
     descompacte e adicione a pasta `bin` ao PATH).
   - Mac: `brew install ffmpeg`
   - Linux: `sudo apt install ffmpeg`
3. As bibliotecas Python. No terminal, dentro desta pasta, rode:
   ```
   pip install flask pillow opencv-python-headless numpy
   ```

Para conferir se o FFmpeg está ok, rode `ffmpeg -version` no terminal. Se
aparecer a versão, está tudo certo.

---

## Como usar (página web — recomendado)

1. Abra o terminal nesta pasta.
2. Rode:
   ```
   python app.py
   ```
3. Abra no navegador: **http://localhost:5000**
4. Arraste **vários reels** de uma vez (os vídeos que você baixou dos
   concorrentes, com o template deles em volta).
5. Para cada vídeo, a ferramenta extrai um quadro e **detecta sozinha** onde
   está o quadradinho do vídeo, mostrando uma caixa azul:
   - Etiqueta verde "recorte automático ✓" = acertou, pode deixar.
   - Etiqueta laranja "confira o recorte ⚠" = não teve certeza, dê uma olhada.
   - Em qualquer caso, você pode **arrastar a caixa** ou **puxar os cantos**
     para ajustar o recorte na hora.
6. Digite a legenda de cada vídeo (a sua legenda, não a do concorrente).
7. Clique em **Gerar todos**. A ferramenta recorta só o quadradinho do vídeo
   (mantendo o movimento e o áudio), descarta o template do concorrente e monta
   tudo no **seu** template.
8. Clique em **Baixar todos (.zip)** — vem um zip com os vídeos prontos,
   nomeados como `post_<nome-do-original>.mp4`.

Dá pra processar de 10 a 30 reels numa tacada. Vídeos sem legenda são pulados
(a ferramenta avisa antes).

Para encerrar, volte ao terminal e aperte `Ctrl + C`.

> Tudo roda no seu próprio computador. Nenhum vídeo é enviado para a internet.

### Sobre a detecção automática

A detecção acerta na maioria dos casos com fundo claro ou escuro uniforme.
Ela tem mais dificuldade quando o vídeo do concorrente é escuro **e** tem
tarjas pretas que se misturam com o fundo da postagem — nesses casos ela marca
"confira o recorte" e você ajusta a caixa em 2 segundos. Por isso o ajuste
manual existe: garante que nenhum recorte sai errado.

---

## Como usar (terminal, sem a página)

```
python meme_maker.py meu_video.mp4 "A legenda do post aqui" resultado.mp4
```

Gera o `resultado.mp4` na mesma pasta.

---

## Trocar a foto de perfil, nome ou @

Abra o arquivo `meme_maker.py` num editor de texto e mude no topo:

```python
PROFILE_NAME   = "Adulto Sofrido"
PROFILE_HANDLE = "@adultosofrido"
```

Para trocar a **foto de perfil**, substitua o arquivo `avatar.png` por outra
imagem quadrada (ela vira círculo automaticamente). Se tiver a foto original em
boa qualidade, use ela — fica mais nítida do que a recortada do print.

## Outros ajustes finos (opcional)

Ainda no topo do `meme_maker.py`:

- `CARD_RADIUS` — quão arredondados são os cantos do vídeo.
- `MARGIN_X` — margem lateral.
- `HEADER_Y` — altura onde começa o cabeçalho (suba ou desça o bloco todo).
- Tamanhos de fonte estão na função `build_overlay` (`f_name`, `f_handle`,
  `f_caption`).

Mexa, salve e gere de novo pra ver o efeito.
