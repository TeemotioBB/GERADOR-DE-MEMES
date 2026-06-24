# Como colocar a ferramenta no Railway (usar pelo iPhone, de qualquer lugar)

Seguindo estes passos, sua ferramenta fica num link na internet que você abre
pelo Safari do iPhone — sem precisar do PC ligado.

## Antes de começar
Você vai precisar de:
- Sua conta do GitHub (que você já tem).
- Uma conta no Railway (cria de graça com o GitHub no passo 2).

---

## Passo 1 — Subir o projeto para o GitHub

1. Acesse https://github.com e clique no **+** no canto superior direito →
   **New repository**.
2. Dê um nome (ex: `gerador-memes`), deixe como **Private** (privado) e clique
   em **Create repository**.
3. Na página seguinte, clique em **uploading an existing file** (link no meio
   da tela).
4. Arraste para lá **todos os arquivos de dentro da pasta `gerador_memes`**
   (não a pasta em si — o conteúdo dela: `app.py`, `meme_maker.py`,
   `detector.py`, `requirements.txt`, `nixpacks.toml`, `Procfile`, a pasta
   `fontes`, a pasta `templates`, o `avatar.png`, etc.).
5. Clique em **Commit changes** no fim da página e espere subir.

> Dica: se o navegador não deixar arrastar pastas (templates, fontes), entre
> em cada uma e suba os arquivos de dentro, ou use o GitHub Desktop.

---

## Passo 2 — Criar a conta no Railway

1. Acesse https://railway.com e clique em **Login**.
2. Escolha **Login with GitHub** e autorize.
3. Você ganha um crédito grátis de US$ 5 para testar, sem cartão.

---

## Passo 3 — Publicar (deploy)

1. No painel do Railway, clique em **New Project**.
2. Escolha **Deploy from GitHub repo**.
3. Selecione o repositório `gerador-memes` que você criou.
4. O Railway vai detectar tudo sozinho (graças aos arquivos `nixpacks.toml` e
   `requirements.txt`) e começar a instalar. Espere alguns minutos — ele instala
   o Python, o FFmpeg e as bibliotecas.
5. Quando terminar, aparece "Success" / "Deployed".

---

## Passo 4 — Pegar o link (a parte importante)

1. No projeto, clique no serviço (o quadradinho que apareceu).
2. Vá na aba **Settings** → seção **Networking** → clique em
   **Generate Domain**.
3. Ele cria um endereço tipo `gerador-memes-production.up.railway.app`.
4. **Esse é o seu link!** Abra ele no Safari do iPhone e use normalmente.

Salve esse link nos favoritos do Safari (ou adicione à tela de início:
botão compartilhar → "Adicionar à Tela de Início") para abrir como se fosse
um app.

---

## Sobre o custo

- O teste é grátis (crédito de US$ 5).
- Depois, o plano Hobby é US$ 5/mês e já inclui US$ 5 de uso. Para uma página
  de memes que processa vídeos de vez em quando, deve ficar nesse piso (~US$ 5,
  uns R$ 30/mês).
- O Railway tem um painel que mostra o consumo em tempo real. Confira de vez em
  quando para não ter surpresa.

## Atualizar a ferramenta no futuro

Se você mudar algo (trocar a foto, ajustar o código), é só subir o arquivo novo
para o mesmo repositório do GitHub. O Railway detecta a mudança e republica
sozinho.

## Importante sobre vídeos grandes

Na nuvem, vídeos muito pesados podem demorar mais para processar do que no seu
PC, e o upload depende da sua internet. Para reels normais (poucos MB), funciona
bem. Se um vídeo muito grande der erro de tempo, tente um arquivo menor.
