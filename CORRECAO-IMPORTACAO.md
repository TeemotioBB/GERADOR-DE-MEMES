# Correção do Failed to fetch na importação do Instagram

A rota POST /importar-instagram agora responde imediatamente com HTTP 202 e um job_id. O download e a análise do Reels acontecem numa thread do servidor. A interface consulta GET /importar-instagram/status/<job_id> a cada 2,5 segundos, tolerando falhas transitórias de conexão.

## Publicação
Envie o conteúdo da pasta GERADOR-DE-MEMES-main ao GitHub, faça o deploy no Railway e recarregue a página com Ctrl+F5. Não é necessário criar novas variáveis.

## Atenção
Isso resolve a dependência de uma requisição longa, mas não contorna bloqueios do Instagram, cookies vencidos, ausência de FFmpeg ou reinícios e falta de memória no Railway. Se continuar falhando, consulte Railway > Deploy Logs para ver a causa. As tarefas ficam na memória de um único processo do Gunicorn e não sobrevivem a reinício de deploy. O Procfile já está configurado com --workers 1.
