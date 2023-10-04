import json
import logging
import os
from io import BytesIO

import redis
import requests
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (CallbackContext, CallbackQueryHandler,
                          CommandHandler, Filters, MessageHandler, Updater)

logging.basicConfig(level=logging.INFO)


class RedisConnection:
    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(RedisConnection, cls).__new__(cls, *args, **kwargs)
            cls._instance.init_redis()
        return cls._instance

    def init_redis(self):
        database_password = os.getenv("DATABASE_PASSWORD")
        database_host = os.getenv("DATABASE_HOST", "localhost")
        database_port = int(os.getenv("DATABASE_PORT", 6379))

        pool = redis.ConnectionPool(
            host=database_host,
            port=database_port,
            password=database_password,
            db=0
        )
        self.connection = redis.Redis(connection_pool=pool)


def start(update: Update, context: CallbackContext) -> None:
    logger.info('start')
    # определяем, откуда пришел запрос - из message или callback_query
    message = update.message if update.message else update.callback_query.message

    products = context.bot_data.get('products', [])
    keyboard = list()
    for product in products:
        button = [
            InlineKeyboardButton(
                product['attributes']['title'],
                callback_data=product['id']
            )
        ]
        keyboard.append(button)
    keyboard.append([InlineKeyboardButton("Моя корзина", callback_data="SHOW_CART")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    message.reply_text('Please choose:', reply_markup=reply_markup)
    return 'HANDLE_MENU'


def handle_menu(update: Update, context: CallbackContext) -> None:
    logger.info('handle_menu')
    query = update.callback_query
    query.answer()
    product_id = query.data
    logger.info(f'product_id={product_id}')
    # Получение данных о продукте с API
    product = get_product_detail(product_id)
    # Скачивание изображения
    image_url = product['attributes']['picture']['data']['attributes']['url']
    image = requests.get(f'http://localhost:{port}{image_url}').content
    image_stream = BytesIO(image)
    default_quantity = 1

    keyboard = generate_keyboard(product_id, default_quantity)
    reply_markup = InlineKeyboardMarkup(keyboard)

    text = f"{product['attributes']['title']}:\n\n{product['attributes']['description']}"
    context.bot.send_photo(
        chat_id=query.message.chat_id,
        photo=image_stream,
        caption=text,
        reply_markup=reply_markup
    )

    # Удаление старого сообщения
    context.bot.delete_message(
        chat_id=query.message.chat_id,
        message_id=query.message.message_id
    )

    return 'HANDLE_DESCRIPTION'


def handle_description(context: CallbackContext, update: Update):
    query = update.callback_query
    logger.info(f'handle_description {context.bot_data}')
    if query.data == 'BACK_TO_MENU':
        start(context, update)
        context.bot.delete_message(
            chat_id=update.effective_chat.id,
            message_id=query.message.message_id
        )
        return 'HANDLE_MENU'
    elif query.data == 'SHOW_CART':
        show_cart(context, update)
        return 'HANDLE_CART'
    else:
        access_token = context.bot_data.get('access_token')
        user_id = query.from_user.id
        product_id = context.user_data.get('product_id')
        name = context.user_data.get('name')
        quantity = int(query.data)
        add_product_to_cart(access_token, user_id, product_id, quantity)
        keyboard = generate_keyboard(product_id, quantity)
        reply_markup = InlineKeyboardMarkup(keyboard)
        context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f'{quantity} pcs {name} added to cart',
            reply_markup=reply_markup
        )
        context.bot.delete_message(
            chat_id=update.effective_chat.id,
            message_id=query.message.message_id
        )
        return 'HANDLE_DESCRIPTION'


def add_to_cart(product_id, chat_id, quantity=1):
    logger.info(f'add_to_cart, chat_id={chat_id} product_id={product_id} quantity={quantity}')
    headers = {'Authorization': f'Bearer {strapi_api_token}'}
    db = RedisConnection().connection

    # Проверка существующей корзины в Redis
    cart_id = db.get(f"cart_id_{chat_id}")
    if cart_id:
        cart_id = cart_id.decode("utf-8")
    else:
        cart_data = {
            "data": {
                "TelegramUserID": str(chat_id),
            }
        }
        response = requests.post(
            "http://localhost:1338/api/carts",
            json=cart_data,
            headers=headers
        )

        if response.status_code != 200:
            logger.info(f"Failed to create cart. Error: {response.content}")
            return None

        cart_id = response.json().get("data").get("id")
        logger.info(f"New cart created with id: {cart_id}")

        # Сохранение cart_id в Redis
        db.set(f"cart_id_{chat_id}", cart_id)

    # Здесь мы добавляем товар в корзину с учетом количества
    add_product_to_cart(cart_id, product_id, quantity)

    return cart_id


def add_product_to_cart(cart_id, product_id, quantity=1):
    logger.info(f'add_product_to_cart, cart_id={cart_id} product_id={product_id}')
    if cart_id is None:
        logger.info("Не удалось добавить товар в корзину: cart_id is None.")
        return
    cart_product_data = {
        "cart": cart_id,
        "product": product_id,
        "quantity": quantity
    }
    headers = {'Authorization': f'Bearer {strapi_api_token}'}
    response = requests.post(
        "http://localhost:1338/api/cart-products",
        json={"data": cart_product_data},
        headers=headers
    )
    if response.status_code == 200:
        logger.info(f"Товар {product_id} успешно добавлен в корзину {cart_id}.")
    else:
        logger.info(f"Не удалось добавить товар в корзину. Ошибка: {response.content}")


def generate_keyboard(product_id, current_quantity):
    return [
        [
            InlineKeyboardButton("-", callback_data=f"decrease_{product_id}"),
            InlineKeyboardButton(str(current_quantity), callback_data=f"quantity_{product_id}"),
            InlineKeyboardButton("+", callback_data=f"increase_{product_id}")
        ],
        [
            InlineKeyboardButton("Добавить в корзину", callback_data=f"add_to_cart_{product_id}")
        ],
        [InlineKeyboardButton("Моя корзина", callback_data="SHOW_CART")],
        [InlineKeyboardButton("Назад", callback_data='BACK_TO_MENU')]
    ]


def handle_users_reply(update, context):
    logger.info('handle_users_reply')
    db = RedisConnection().connection
    if update.message:
        user_reply = update.message.text
        logger.info(f'user_reply={user_reply}')
        chat_id = update.message.chat_id
    elif update.callback_query:
        user_reply = update.callback_query.data
        chat_id = update.callback_query.message.chat_id
        query = update.callback_query

        if "increase_" in user_reply:
            product_id = user_reply.split("_")[1]
            # Получаем текущее количество
            key = f"quantity_{product_id}"
            current_quantity = context.user_data.get(key, 1)

            # Увеличиваем количество
            current_quantity += 1
            context.user_data[key] = current_quantity
             # Обновляем клавиатуру
            keyboard = generate_keyboard(product_id, current_quantity)
            query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))

        elif "decrease_" in user_reply:
            product_id = user_reply.split("_")[1]
            # Получаем текущее количество
            key = f"quantity_{product_id}"
            current_quantity = context.user_data.get(key, 1)

            # Уменьшаем количество (но не меньше 1)
            current_quantity = max(1, current_quantity - 1)
            context.user_data[key] = current_quantity

            # Обновляем клавиатуру
            keyboard = generate_keyboard(product_id, current_quantity)
            query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))

        if "add_to_cart_" in user_reply:
            product_id = user_reply.split("_")[-1]
            logger.info('Добавляю в бд')
            key = f"quantity_{product_id}"
            current_quantity = context.user_data.get(key, 1)
            cart_id = add_to_cart(product_id, chat_id, current_quantity)
            if cart_id is None:
                logger.error("Ошибка при добавлении в корзину: cart_id is None")
    else:
        return

    if user_reply == '/start':
        user_state = 'START'
    elif user_reply == 'SHOW_CART':
        user_state = 'SHOW_CART'
    else:
        user_state = db.get(chat_id).decode("utf-8")

    states_functions = {
        'START': start,
        'HANDLE_MENU': handle_menu,
        'HANDLE_DESCRIPTION': handle_description,
        'SHOW_CART': show_cart
    }
    if user_state in states_functions:
        state_handler = states_functions[user_state]
    else:
        logger.error(f"Неизвестный user_state: {user_state}")
        return

    try:
        next_state = state_handler(update, context)
        if not ("increase_" in user_reply or "decrease_" in user_reply):
            db.set(chat_id, next_state)
    except Exception as err:
        print('Ошибка', err)


def get_products(port, strapi_api_token):
    logger.info('get_products')
    db = RedisConnection().connection
    cache_key = "products_list"
    cached_data = db.get(cache_key)

    if cached_data:
        return json.loads(cached_data.decode("utf-8"))

    url = f'http://localhost:{port}/api/products?_populate=*'
    header = {'Authorization': f'Bearer {strapi_api_token}'}
    response = requests.get(url, headers=header)
    response.raise_for_status()
    response_json = response.json()['data']

    db.setex(cache_key, 3600, json.dumps(response_json))
    return response_json


def get_product_detail(product_id):
    logger.info('get_product_detail')
    cache_key = f"product_detail_{product_id}"
    db = RedisConnection().connection
    cached_data = db.get(cache_key)

    if cached_data:
        return json.loads(cached_data.decode("utf-8"))

    url = f'http://localhost:{port}/api/products/{product_id}'
    header = {'Authorization': f'Bearer {strapi_api_token}'}
    payload = {'populate': '*'}
    response = requests.get(url, headers=header, params=payload)
    response.raise_for_status()
    response_json = response.json()['data']

    db.setex(cache_key, 3600, json.dumps(response_json))

    return response_json


def get_cart_contents(cart_id):
    logger.info('get_cart_contents')
    headers = {'Authorization': f'Bearer {strapi_api_token}'}
    payload = {'populate': 'cart_products.product'}
    response = requests.get(
        f'http://localhost:1338/api/carts/{cart_id}',
        headers=headers,
        params=payload
    )
    logger.info(f'response.status_code = {response.status_code}')
    if response.status_code != 200:
        logger.info(f"Failed to fetch cart contents. Error: {response.content}")
        return None
    cart_contents = response.json()["data"]["attributes"]["cart_products"]["data"]
    return cart_contents


def show_cart(update: Update, context: CallbackContext):
    logger.info('show_cart')
    db = RedisConnection().connection
    chat_id = update.message.chat_id if update.message else update.callback_query.message.chat_id
    cart_id = db.get(f"cart_id_{chat_id}")

    if cart_id:
        cart_id = cart_id.decode("utf-8")
        logger.info(f'cart_id = {cart_id}')
        cart_contents = get_cart_contents(cart_id)
        if cart_contents:
            cart_text = "В вашей корзине:\n"
            for cart_content in cart_contents:
                product_id = cart_content["attributes"]["product"]["data"]["id"]
                product_detail = get_product_detail(product_id)
                cart_text += f"{product_detail['attributes']['title']}, цена: {product_detail['attributes']['price']}, количество: {cart_content['attributes']['quantity']}\n"
            context.bot.send_message(chat_id=chat_id, text=cart_text)
        else:
            context.bot.send_message(chat_id=chat_id, text="Ваша корзина пуста.")
    else:
        context.bot.send_message(chat_id=chat_id, text="Ваша корзина пуста.")
    return 'HANDLE_MENU'


if __name__ == '__main__':
    logger = logging.getLogger(__name__)
    load_dotenv()
    token = os.getenv("TELEGRAM_TOKEN")
    strapi_api_token = os.getenv('STRAPI_API_TOKEN')
    port = os.getenv('PORT')
    updater = Updater(token)
    dispatcher = updater.dispatcher
    logger.info("Starting to get products...")
    products = get_products(port, strapi_api_token)
    logger.info("Finished getting products.")
    dispatcher.bot_data['products'] = products

    dispatcher.add_handler(CallbackQueryHandler(handle_users_reply))
    dispatcher.add_handler(MessageHandler(Filters.text, handle_users_reply))
    dispatcher.add_handler(CommandHandler('start', handle_users_reply))
    updater.start_polling()
    updater.idle()
