import os
import requests
import asyncio
import time
from PIL import Image, ImageDraw, ImageFont
from telegram import Bot
from dotenv import load_dotenv
import random
import signal

load_dotenv()

# Configurações globais
RECONNECT_DELAY = 60  # 1 minuto entre tentativas de reconexão
POST_INTERVAL = 6 * 60 * 60  # 6 horas entre posts
MAX_RETRIES = 5  # Máximo de tentativas de reconexão

# Variáveis de ambiente
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME")
SHRINKME_API = os.getenv("SHRINKME_API")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GOOGLE_CX = os.getenv("GOOGLE_CX")

if not all([BOT_TOKEN, CHANNEL_USERNAME]):
    raise ValueError("Variáveis de ambiente essenciais não configuradas!")

class BotManager:
    def __init__(self):
        self.bot = None
        self.should_restart = True
        self.posted_coupons = set()  # Conjunto para armazenar títulos de cupons já postados
        signal.signal(signal.SIGINT, self.handle_exit)
        signal.signal(signal.SIGTERM, self.handle_exit)

    def handle_exit(self, signum, frame):
        print(f"\nRecebido sinal {signum}, encerrando...")
        self.should_restart = False

    async def initialize_bot(self):
        self.bot = Bot(token=BOT_TOKEN)
        try:
            await self.test_connection()
            return True
        except Exception as e:
            print(f"Falha na inicialização: {e}")
            return False

    async def test_connection(self):
        print("Testando conexão com o Telegram...")
        chat = await self.bot.get_chat(chat_id=CHANNEL_USERNAME)
        print(f"Canal: {chat.title} (ID: {chat.id})")
        await self.bot.send_message(
            chat_id=CHANNEL_USERNAME,
            text="🤖 Bot reconectado com sucesso!"
        )
        print("Teste de conexão bem-sucedido!")

    async def buscar_cupons_google(self, site_query):
        """Busca cupons usando a Google Custom Search API para um site específico."""
        try:
            query = f"site:{site_query} cupons desconto"
            url = f"https://www.googleapis.com/customsearch/v1?key={GOOGLE_API_KEY}&cx={GOOGLE_CX}&q={query}&num=10"
            response = requests.get(url)
            response.raise_for_status()
            results = response.json().get("items", [])
            coupons = [
                {
                    "titulo": item["title"],
                    "descricao": item["snippet"],
                    "link": item["link"],
                    "imagem": item.get("pagemap", {}).get("cse_image", [{}])[0].get("src", None),
                    "fonte": site_query
                }
                for item in results
            ]
            print(f"Resultados encontrados em {site_query}: {len(coupons)}")
            return coupons
        except Exception as e:
            print(f"Erro ao buscar cupons em {site_query}: {e}")
            return []

    async def get_cupons(self):
        """Obtém cupons de múltiplas fontes."""
        try:
            # Lista de sites de cupons
            sites = [
                "cuponomia.com",
                "meliuz.com.br",
                "pelando.com.br"
            ]
            all_coupons = []
            for site in sites:
                coupons = await self.buscar_cupons_google(site)
                all_coupons.extend(coupons)
            random.shuffle(all_coupons)  # Mistura os cupons de todas as fontes
            print(f"Cupons totais encontrados: {len(all_coupons)}")
            return all_coupons
        except Exception as e:
            print(f"Erro ao obter cupons: {e}")
            return []

    async def post_cupons(self):
        """Posta um único cupom no canal do Telegram a cada ciclo."""
        try:
            print("🔎 Buscando cupons...")
            cupons = await self.get_cupons()
            if not cupons:
                print("Nenhum cupom encontrado.")
                return

            # Filtra cupons não postados
            new_coupons = [c for c in cupons if c["titulo"] not in self.posted_coupons]
            if not new_coupons:
                print("Nenhum cupom novo disponível. Limpando histórico...")
                self.posted_coupons.clear()  # Limpa o histórico se não houver novos cupons
                new_coupons = cupons

            if new_coupons:  # Garante que apenas um cupom seja postado por ciclo
                cupom = new_coupons[0]  # Usa o primeiro cupom novo
                try:
                    titulo = cupom["titulo"]
                    descricao = cupom["descricao"]
                    link = shorten_url(cupom["link"])
                    caption = f"🎁 {titulo}\n\n📝 {descricao}\n\n🔗 {link}\n\n}"

                    # Tenta baixar imagem do cupom, se disponível
                    imagem_gerada = False
                    if cupom.get("imagem"):
                        imagem_gerada = download_image(cupom["imagem"], "cupom.png")
                    if not imagem_gerada:
                        create_image(titulo)  # Gera imagem local se não houver imagem online

                    # Verifica se a imagem existe antes de enviar
                    if os.path.exists("cupom.png"):
                        with open("cupom.png", "rb") as img:
                            await self.bot.send_photo(
                                chat_id=CHANNEL_USERNAME,
                                photo=img,
                                caption=caption
                            )
                        print(f"📤 Postado com imagem de {cupom['fonte']}: {titulo}")
                    else:
                        # Fallback: envia apenas o texto
                        await self.bot.send_message(
                            chat_id=CHANNEL_USERNAME,
                            text=caption
                        )
                        print(f"📤 Postado sem imagem de {cupom['fonte']} (imagem não encontrada): {titulo}")

                    self.posted_coupons.add(titulo)  # Adiciona ao histórico
                    print(f"Cupons postados até agora: {len(self.posted_coupons)}")

                except Exception as e:
                    print(f"Erro ao postar cupom '{titulo}' de {cupom['fonte']}: {e}")
            else:
                print("Nenhum novo cupom para postar.")

        except Exception as e:
            print(f"Erro geral no post_cupons: {e}")
            raise

    async def run(self):
        retry_count = 0

        while self.should_restart:
            try:
                if not await self.initialize_bot():
                    raise Exception("Falha na inicialização do bot")

                retry_count = 0  # Resetar contador após conexão bem-sucedida
                print("\n✅ Bot operacional. Pressione Ctrl+C para encerrar.")

                while self.should_restart:
                    try:
                        await self.post_cupons()
                        print(f"Aguardando próximo ciclo em {POST_INTERVAL} segundos...")
                        await asyncio.sleep(POST_INTERVAL)
                    except Exception as e:
                        print(f"\n⚠️ Erro durante operação: {e}")
                        print(f"Reconectando em {RECONNECT_DELAY} segundos...")
                        await asyncio.sleep(RECONNECT_DELAY)
                        break

            except Exception as e:
                retry_count += 1
                print(f"\n❌ Erro crítico (Tentativa {retry_count}/{MAX_RETRIES}): {e}")

                if retry_count >= MAX_RETRIES:
                    print("Número máximo de tentativas alcançado. Encerrando.")
                    self.should_restart = False
                    break

                print(f"Tentando novamente em {RECONNECT_DELAY} segundos...")
                await asyncio.sleep(RECONNECT_DELAY)

def create_image(titulo):
    """Gera uma imagem para o cupom (implementação de exemplo)."""
    try:
        img = Image.new('RGB', (800, 400), color='white')
        d = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("arial.ttf", 40)
        except Exception as e:
            print(f"Fonte arial.ttf não encontrada, usando fonte padrão: {e}")
            font = ImageFont.load_default()
        d.text((10, 10), titulo, fill='black', font=font)
        img.save("cupom.png")
        print("Imagem gerada: cupom.png")
    except Exception as e:
        print(f"Erro ao criar imagem: {e}")

def download_image(url, output_path):
    """Baixa uma imagem de uma URL e salva no caminho especificado."""
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        with open(output_path, "wb") as f:
            f.write(response.content)
        print(f"Imagem baixada com sucesso: {output_path}")
        return True
    except Exception as e:
        print(f"Erro ao baixar imagem de {url}: {e}")
        return False

def shorten_url(url):
    """Encurta a URL usando a API ShrinkMe."""
    if not SHRINKME_API:
        print("Chave da API ShrinkMe não configurada. Retornando URL original.")
        return url
    try:
        api_url = "https://shrinkme.io/api"
        params = {"api": SHRINKME_API, "url": url}
        response = requests.get(api_url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data.get("status") == "success":
            return data.get("shortenedUrl", url)
        else:
            print(f"Erro na API ShrinkMe: {data.get('message', 'Resposta inválida')}")
            return url
    except Exception as e:
        print(f"Erro ao encurtar URL: {e}")
        return url

if __name__ == "__main__":
    bot_manager = BotManager()
    try:
        asyncio.run(bot_manager.run())
    except KeyboardInterrupt:
        print("\n🛑 Bot encerrado pelo usuário")
    except Exception as e:
        print(f"\n💥 Erro fatal: {e}")
    finally:
        print("Encerrando todos os processos...")
