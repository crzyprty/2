import logging
import requests
import json
import heapq
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
    ConversationHandler,
    CallbackQueryHandler
)

TOKEN = "7567906773:AAHAKtb0a_418G8I_0lIpqVrUmufkMZMZ-s"

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

SELECTING_ACTION, AWAITING_INPUT = range(2)

API_URL_BASE = "https://api.geckoterminal.com/api/v2/networks/solana/pools?include=base_token,dex"
MAX_PAGES_TO_FETCH = 5

RESTART_BUTTON_TEXT = "🔄 Botu Yeniden Başlat"

def build_keyboard(user_data):
    min_liq = user_data.get('min_liquidity')
    max_liq = user_data.get('max_liquidity')
    limit = user_data.get('limit')
    min_button_text = f"1. Min Likidite ({min_liq:,.0f} USD)" if min_liq is not None else "1. Min Likidite Ayarla"
    max_button_text = f"2. Max Likidite ({max_liq:,.0f} USD)" if max_liq is not None else "2. Max Likidite Ayarla"
    limit_button_text = f"3. Limit ({limit} adet)" if limit is not None else "3. Listeleme Limiti Ayarla"
    keyboard = [[InlineKeyboardButton(min_button_text, callback_data='set_min_liq')],[InlineKeyboardButton(max_button_text, callback_data='set_max_liq')],[InlineKeyboardButton(limit_button_text, callback_data='set_limit')],]
    if min_liq is not None and max_liq is not None and limit is not None:
        keyboard.append([InlineKeyboardButton("🚀 Çalıştır", callback_data='run_query')])
    keyboard.append([InlineKeyboardButton("❌ İptal", callback_data='cancel')])
    return InlineKeyboardMarkup(keyboard)

def build_status_message(user_data):
    min_liq = user_data.get('min_liquidity')
    max_liq = user_data.get('max_liquidity')
    limit = user_data.get('limit')
    message = "Filtreleme Ayarları:\n"
    message += f"  - Min Likidite: {min_liq:,.0f} USD\n" if min_liq is not None else "  - Min Likidite: Ayarlanmadı\n"
    message += f"  - Max Likidite: {max_liq:,.0f} USD\n" if max_liq is not None else "  - Max Likidite: Ayarlanmadı\n"
    message += f"  - Listeleme Limiti: {limit} adet\n" if limit is not None else "  - Listeleme Limiti: Ayarlanmadı\n"
    message += "\nLütfen ayarlamak istediğiniz seçeneği seçin veya tümü ayarlandıysa 'Çalıştır'a basın."
    return message

async def filter_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    should_clear_all_user_data = False
    initiator_info = "Bilinmeyen tetikleyici"

    if update.message and update.message.text == RESTART_BUTTON_TEXT:
        should_clear_all_user_data = True
        initiator_info = f"Buton: {RESTART_BUTTON_TEXT}"
    elif update.callback_query and update.callback_query.data == 'new_query':
        should_clear_all_user_data = True
        initiator_info = f"Callback: new_query"
    elif update.message and update.message.text and update.message.text.startswith('/filter'):
        should_clear_all_user_data = True
        initiator_info = f"Komut: {update.message.text}"

    if should_clear_all_user_data:
        context.user_data.clear()
        logger.info(f"Tüm kullanıcı verileri temizlendi. Tetikleyici: {initiator_info}")
    
    user_data = context.user_data
    keyboard = build_keyboard(user_data)
    message_text = build_status_message(user_data)
    chat_id = update.effective_chat.id
    new_message_sent = False

    if update.callback_query:
        query = update.callback_query
        await query.answer()
        try:
            await query.message.edit_text(message_text, reply_markup=keyboard, parse_mode='Markdown')
            user_data['message_id'] = query.message.message_id
        except Exception as e:
            logger.error(f"filter_start (callback) mesaj düzenleme hatası: {e}. Yeni mesaj gönderiliyor.")
            sent_message = await context.bot.send_message(chat_id=chat_id, text=message_text, reply_markup=keyboard, parse_mode='Markdown')
            user_data['message_id'] = sent_message.message_id
            new_message_sent = True
    elif update.message:
        sent_message = await update.message.reply_text(message_text, reply_markup=keyboard, parse_mode='Markdown')
        user_data['message_id'] = sent_message.message_id
        new_message_sent = True

    if not new_message_sent and 'message_id' not in user_data and update.effective_message:
        logger.warning("filter_start: Yeni mesaj gönderilmedi ve user_data'da message_id yok. Yeni mesaj gönderiliyor.")
        sent_message = await context.bot.send_message(chat_id=chat_id, text=message_text, reply_markup=keyboard, parse_mode='Markdown')
        user_data['message_id'] = sent_message.message_id

    return SELECTING_ACTION

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    action = query.data
    user_data = context.user_data
    chat_id = query.message.chat_id
    user_data['message_id'] = query.message.message_id
    original_message_id = user_data.get('message_id') 
    if action == 'cancel':
        try:
            await query.message.edit_text(text="Filtreleme işlemi iptal edildi.")
        except Exception as e:
            logger.warning(f"İptal mesajı düzenlenemedi: {e}")
            await context.bot.send_message(chat_id=chat_id, text="Filtreleme işlemi iptal edildi.") 
        final_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Yeni Sorgu", callback_data='new_query')]])
        await context.bot.send_message(chat_id=chat_id, text="Yeni bir sorgu yapmak için:", reply_markup=final_keyboard)
        user_data.clear()
        return ConversationHandler.END
    if action == 'run_query':
        if not all(k in user_data for k in ('min_liquidity', 'max_liquidity', 'limit')):
            await context.bot.send_message(chat_id=chat_id, text="Hata: Çalıştırmadan önce tüm değerler ayarlanmalı.", reply_to_message_id=original_message_id if original_message_id else None)
            return SELECTING_ACTION
        try:
            await query.message.edit_text(text="Veriler alınıyor (birden fazla sayfa taranabilir), lütfen bekleyin...", reply_markup=None) 
        except Exception as e:
            logger.warning(f"Run query 'bekleyin' mesajı gösterilemedi: {e}")
            await context.bot.send_message(chat_id=chat_id, text="Veriler alınıyor, lütfen bekleyin...") 
        return await run_api_query(update, context) 
    if action in ['set_min_liq', 'set_max_liq', 'set_limit']:
        user_data['next_action'] = action
        prompt_text = ""
        if action == 'set_min_liq': prompt_text = "Lütfen minimum likidite miktarını yazın (USD, örn: 10000):"
        elif action == 'set_max_liq': prompt_text = "Lütfen maksimum likidite miktarını yazın (USD, örn: 50000):"
        elif action == 'set_limit': prompt_text = "Lütfen listelemek istediğiniz maksimum coin sayısını yazın (örn: 10):"
        try:
            await query.message.edit_text(text=prompt_text, reply_markup=None)
        except Exception as e:
            logger.warning(f"Input isteme mesajı gösterilemedi: {e}")
            await context.bot.send_message(chat_id=chat_id, text=prompt_text) 
        return AWAITING_INPUT
    keyboard = build_keyboard(user_data)
    message_text = build_status_message(user_data)
    try:
        await query.message.edit_text(text=message_text, reply_markup=keyboard, parse_mode='Markdown')
    except Exception as e: logger.warning(f"Bilinmeyen callback sonrası klavye güncellenemedi: {e}")
    return SELECTING_ACTION

async def receive_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_input = update.message.text
    user_data = context.user_data
    action_to_set = user_data.get('next_action')
    chat_id = update.effective_chat.id
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=update.message.message_id)
        logger.info(f"Kullanıcı girdi mesajı ({update.message.message_id}) silindi.")
    except Exception as e:
        logger.warning(f"Kullanıcı girdi mesajı silinemedi: {e}")
    original_message_id = user_data.get('message_id') 
    if not action_to_set:
        error_text = "Bir hata oluştu (beklenen eylem bulunamadı). /start veya /filter ile tekrar deneyin."
        if original_message_id:
            try: await context.bot.edit_message_text(chat_id=chat_id, message_id=original_message_id, text=error_text)
            except Exception: await context.bot.send_message(chat_id=chat_id, text=error_text)
        else: await context.bot.send_message(chat_id=chat_id, text=error_text)
        user_data.clear()
        return ConversationHandler.END
    current_prompt_map = {
        'set_min_liq': "Lütfen minimum likidite miktarını yazın (USD, örn: 10000):",
        'set_max_liq': "Lütfen maksimum likidite miktarını yazın (USD, örn: 50000):",
        'set_limit': "Lütfen listelemek istediğiniz maksimum coin sayısını yazın (örn: 10):"
    }
    current_prompt = current_prompt_map.get(action_to_set, "Lütfen geçerli bir değer girin:")
    try:
        value_str = user_input.replace(',', '').replace('.', '') 
        value = float(value_str) if action_to_set != 'set_limit' else int(value_str)
        error_message = None
        if value < 0: error_message = "Lütfen pozitif bir değer girin."
        elif action_to_set == 'set_min_liq':
            max_liq = user_data.get('max_liquidity')
            if max_liq is not None and value > max_liq: error_message = f"Minimum likidite ({value:,.0f}), maksimumdan ({max_liq:,.0f}) büyük olamaz."
            else: user_data['min_liquidity'] = value
        elif action_to_set == 'set_max_liq':
            min_liq = user_data.get('min_liquidity')
            if min_liq is not None and value < min_liq: error_message = f"Maksimum likidite ({value:,.0f}), minimumdan ({min_liq:,.0f}) küçük olamaz."
            else: user_data['max_liquidity'] = value
        elif action_to_set == 'set_limit':
             if value <= 0: error_message = "Limit 0'dan büyük olmalı."
             else: user_data['limit'] = int(value)
        if error_message:
            prompt_text = f"❌ {error_message}\n\n{current_prompt}"
            if original_message_id:
                try: await context.bot.edit_message_text(chat_id=chat_id, message_id=original_message_id, text=prompt_text, reply_markup=None)
                except Exception as e: logger.warning(f"Input hata mesajı (değer) düzenlenemedi: {e}")
            else: 
                sent_msg = await context.bot.send_message(chat_id=chat_id, text=prompt_text)
                user_data['message_id'] = sent_msg.message_id 
            return AWAITING_INPUT 
        del user_data['next_action']
        logger.info(f"Kullanıcı verisi güncellendi: {action_to_set} = {value}")
        keyboard = build_keyboard(user_data)
        message_text = build_status_message(user_data)
        if original_message_id:
            try:
                await context.bot.edit_message_text(chat_id=chat_id, message_id=original_message_id, text=message_text, reply_markup=keyboard, parse_mode='Markdown')
            except Exception as e:
                logger.warning(f"Input sonrası menü güncellenemedi (mesaj ID: {original_message_id}): {e}. Yeni mesaj gönderiliyor.")
                sent_message = await context.bot.send_message(chat_id=chat_id, text=message_text, reply_markup=keyboard, parse_mode='Markdown')
                user_data['message_id'] = sent_message.message_id 
        else: 
            logger.warning("Orijinal mesaj ID'si bulunamadı, yeni menü mesajı gönderiliyor.")
            sent_message = await context.bot.send_message(chat_id=chat_id, text=message_text, reply_markup=keyboard, parse_mode='Markdown')
            user_data['message_id'] = sent_message.message_id
        return SELECTING_ACTION
    except ValueError:
        prompt_text = f"❌ Geçersiz sayı formatı. Lütfen sadece rakam girin.\n\n{current_prompt}"
        if original_message_id:
            try: await context.bot.edit_message_text(chat_id=chat_id, message_id=original_message_id, text=prompt_text, reply_markup=None)
            except Exception as e: logger.warning(f"Input format hata mesajı düzenlenemedi: {e}")
        else:
            sent_msg = await context.bot.send_message(chat_id=chat_id, text=prompt_text)
            user_data['message_id'] = sent_msg.message_id
        return AWAITING_INPUT

async def run_api_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_data = context.user_data
    min_liq = user_data['min_liquidity']
    max_liq = user_data['max_liquidity']
    result_limit = user_data['limit']
    chat_id = update.effective_chat.id
    final_message_text = "Yeni bir sorgu yapmak için aşağıdaki butonu kullanın:"
    final_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Yeni Sorgu", callback_data='new_query')]])
    top_pools_data = [] 
    current_page = 1
    pools_processed_count = 0
    api_request_successful = False
    try:
        while current_page <= MAX_PAGES_TO_FETCH:
            api_url = f"{API_URL_BASE}&page={current_page}"
            logger.info(f"Sayfa {current_page} API'den çekiliyor: {api_url}")
            headers = {'Accept': 'application/json;version=20230302', 'User-Agent': 'MyTelegramBot/1.0'}             
            response = requests.get(api_url, headers=headers, timeout=30) 
            response.raise_for_status() 
            response_data = response.json()
            api_request_successful = True 
            if not response_data or 'data' not in response_data or not response_data['data']:
                logger.info(f"API sayfa {current_page} boş veya veri yok, döngüden çıkılıyor.")
                break
            included_data = {item['id']: item for item in response_data.get('included', [])}
            for pool in response_data['data']:
                pools_processed_count += 1
                attributes = pool.get('attributes', {})
                relationships = pool.get('relationships', {})
                liquidity_usd_str = attributes.get('reserve_in_usd', '0')
                try:
                    liquidity_usd = float(liquidity_usd_str)
                except ValueError:
                    logger.warning(f"Havuz {pool.get('id')} için geçersiz likidite değeri: {liquidity_usd_str}")
                    continue 
                if min_liq <= liquidity_usd <= max_liq:
                    pool_id = pool.get('id', 'N/A_ID')
                    base_token_info = {}
                    base_token_address = 'N/A'
                    base_token_rel = relationships.get('base_token', {}).get('data', {})
                    if base_token_rel and base_token_rel.get('id'):
                        base_token_data = included_data.get(base_token_rel.get('id'))
                        if base_token_data:
                            base_token_info = base_token_data.get('attributes',{})
                            base_token_address = base_token_info.get('address', base_token_rel.get('id').split('_')[-1] if '_' in base_token_rel.get('id') else 'N/A')
                    token_name = base_token_info.get('name', attributes.get('name','Bilinmiyor'))
                    token_symbol = base_token_info.get('symbol', 'N/A')
                    dex_name = 'Bilinmiyor'
                    dex_rel = relationships.get('dex', {}).get('data', {})
                    if dex_rel and dex_rel.get('id'):
                        dex_data = included_data.get(dex_rel.get('id'))
                        if dex_data:
                            dex_name = dex_data.get('attributes', {}).get('name', 'Bilinmiyor')
                    price_usd_str = attributes.get('base_token_price_usd', '0')
                    try:
                        price_usd = float(price_usd_str)
                    except ValueError:
                        price_usd = 0.0
                        logger.warning(f"Havuz {pool_id} için geçersiz fiyat değeri: {price_usd_str}")
                    pool_data_dict = {
                        'name': token_name,
                        'symbol': token_symbol,
                        'liquidity': liquidity_usd,
                        'price': price_usd,
                        'dex': dex_name,
                        'address': base_token_address,
                        'pool_id_for_url': pool_id 
                    }
                    if len(top_pools_data) < result_limit:
                        heapq.heappush(top_pools_data, (-liquidity_usd, pool_data_dict))
                    else:
                        if -liquidity_usd < top_pools_data[0][0]: 
                            heapq.heapreplace(top_pools_data, (-liquidity_usd, pool_data_dict))
            current_page += 1
            if current_page > MAX_PAGES_TO_FETCH :
                logger.info(f"MAX_PAGES_TO_FETCH ({MAX_PAGES_TO_FETCH}) sınırına ulaşıldı.")
                break
        logger.info(f"Toplam {pools_processed_count} havuz {current_page-1} sayfada işlendi.")
        if not top_pools_data:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"Belirtilen likidite aralığında (${min_liq:,.0f} - ${max_liq:,.0f}) havuz bulunamadı (Toplam {pools_processed_count} havuz tarandı)."
            )
        else:
            results_to_display = []
            while top_pools_data:
                neg_liq, data_dict = heapq.heappop(top_pools_data)
                results_to_display.append((-neg_liq, data_dict))
            results_to_display.reverse() 
            result_texts = []
            for liquidity_val, data_dict in results_to_display:
                 gecko_link = f"https://www.geckoterminal.com/solana/pools/{data_dict['pool_id_for_url'].replace('solana_', '')}" 
                 result_texts.append(
                    f"🪙 *{data_dict['name']} ({data_dict['symbol']})*\n"
                    f"💧 Likidite: ${data_dict['liquidity']:,.2f}\n"
                    f"💲 Fiyat (Base): ${data_dict['price']:.6f}\n" 
                    f"🔗 DEX: {data_dict['dex']}\n"
                    f"📄 Kontrat: `{data_dict['address']}`\n"
                    f"🦎 [GeckoTerminal]({gecko_link})"
                 )
            response_header = (
                f"✅ Likiditesi ${min_liq:,.0f} - ${max_liq:,.0f} arasında olan en iyi {len(result_texts)} havuz bulundu (max {result_limit}, likiditeye göre sıralı):\n\n"
            )
            full_message = response_header
            for i, text_part in enumerate(result_texts):
                if len(full_message) + len(text_part) + 15 > 4090: 
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=full_message,
                        parse_mode='Markdown',
                        disable_web_page_preview=True
                    )
                    full_message = text_part 
                else:
                    if i > 0 : 
                         full_message += "\n\n---\n\n" + text_part
                    else:
                         full_message += text_part
            if full_message:
                 await context.bot.send_message(
                    chat_id=chat_id,
                    text=full_message,
                    parse_mode='Markdown',
                    disable_web_page_preview=True
                )
    except requests.exceptions.HTTPError as e:
        logger.error(f"API HTTP hatası (Sayfa {current_page}): {e.response.status_code} - {e.response.text[:200]}")
        error_text = f"API hatası ({e.response.status_code} - Sayfa {current_page}). Sunucudan dönen mesaj: "
        try:
            error_details = e.response.json().get('errors', [{}])[0].get('title', 'Detay yok')
            error_text += error_details
        except json.JSONDecodeError:
            error_text += e.response.text[:100] 
        await 
