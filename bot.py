import os
import telebot
from telebot import types
import requests
from bs4 import BeautifulSoup
import pandas as pd
import matplotlib.pyplot as plt
import geopandas as gpd
from shapely.geometry import Point

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def proverka(resp):
    if resp.status_code != 200:
        raise Exception("Ошибка доступа к сайту. Сайт недоступен.")
    else:
        print("Доступ к сайту получен")

# --- ИНИЦИАЛИЗАЦИЯ БОТА ---

# Читаем токен из переменных окружения
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("❌ Токен не найден! Установите переменную окружения BOT_TOKEN")

bot = telebot.TeleBot(BOT_TOKEN)

# --- ОБРАБОТЧИКИ КОМАНД ---

@bot.message_handler(commands=["start", "help"])
def send_welcome(message):
    # Создаем клавиатуру с кнопками
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    btn1 = types.KeyboardButton("Список регионов")
    btn2 = types.KeyboardButton("Помощь")
    markup.add(btn1, btn2)

    welcome_text = (
        "Привет! Этот бот создаст вам карту давления в регионах России.\n"
        "Повышенное давление (>1013.25 гПа) — красные точки, пониженное — синие.\n\n"
        "Просто напишите название региона или нажмите кнопку ниже."
    )
    bot.send_message(message.chat.id, welcome_text, reply_markup=markup)

@bot.message_handler(
    func=lambda message: message.text == "Список регионов" or message.text == "/regions"
)
def list_regions(message):
    bot.send_message(message.chat.id, "Загружаю список регионов...")
    try:
        sait_rus = "http://www.pogodaiklimat.ru/archive.php?id=ru"
        resp_rus = requests.get(sait_rus)
        resp_rus.encoding = "utf-8"
        soup = BeautifulSoup(resp_rus.text, "lxml")

        div_list = soup.find("div", class_="big-blue-billet__list-container")
        links = div_list.find_all("a")
        regions_list = [link.string for link in links if link.string]

        response = "Доступные регионы:\n\n" + ", ".join(regions_list)

        # Telegram ограничивает сообщения 4096 символами
        if len(response) > 4000:
            for x in range(0, len(response), 4000):
                bot.send_message(message.chat.id, response[x : x + 4000])
        else:
            bot.send_message(message.chat.id, response)
    except Exception as e:
        bot.send_message(message.chat.id, f"Ошибка при получении списка: {e}")

@bot.message_handler(func=lambda message: message.text == "Помощь")
def help_command(message):
    bot.send_message(
        message.chat.id,
        "Введите точное название региона (например: Чувашия). Список можно узнать по кнопке.",
    )

# --- ОСНОВНАЯ ЛОГИКА ОБРАБОТКИ РЕГИОНА ---

@bot.message_handler(content_types=["text"])
def handle_text(message):
    # Игнорируем системные кнопки, для них есть свои обработчики выше
    if message.text in ["Список регионов", "Помощь"]:
        return

    region = message.text
    bot.send_message(message.chat.id, f"Начинаю обработку региона: {region}...")

    try:
        # 1. Поиск ссылки на регион
        sait_rus = "http://www.pogodaiklimat.ru/archive.php?id=ru"
        resp_rus = requests.get(sait_rus)
        proverka(resp_rus)
        resp_rus.encoding = "utf-8"

        soup = BeautifulSoup(resp_rus.text, "lxml")
        reg_link_tag = soup.find("a", string=region)

        if not reg_link_tag:
            bot.send_message(
                message.chat.id,
                f'Регион "{region}" не найден. Проверьте правильность написания.',
            )
            return

        href_reg = "http://www.pogodaiklimat.ru" + reg_link_tag.get("href")

        # 2. Получение списка станций
        resp_reg = requests.get(href_reg)
        resp_reg.encoding = "utf-8"
        soup_n = BeautifulSoup(resp_reg.text, "lxml")
        div_stations = soup_n.find("div", class_="big-blue-billet__list-container")
        station_links = div_stations.find_all("a")

        list_pressure, list_lat, list_long = [], [], []

        bot.send_message(
            message.chat.id, f"Найдено станций: {len(station_links)}. Собираю данные..."
        )

        # 3. Сбор данных по каждой станции
        for a in station_links:
            try:
                href_station = "http://www.pogodaiklimat.ru" + a.get("href")

                # Чтение таблицы данных
                df_list = pd.read_html(
                    href_station, converters={0: str, 1: str}, skiprows=1
                )
                if len(df_list) < 2:
                    continue

                df = df_list[1]
                # Давление обычно в 12-й колонке (индекс 11)
                pressure_val = df.iloc[-1, 11]

                if pd.isna(pressure_val) or pressure_val == "":
                    continue

                # Координаты со страницы станции
                resp_s = requests.get(href_station)
                resp_s.encoding = "utf-8"
                soup_s = BeautifulSoup(resp_s.text, "lxml")
                archive_div = soup_s.find("div", class_="archive-text")
                spans = archive_div.find_all("span")

                list_lat.append(float(spans[1].string))
                list_long.append(float(spans[2].string))
                list_pressure.append(float(pressure_val))

            except Exception:
                continue  # Если одна станция упала, идем к следующей

        if not list_pressure:
            bot.send_message(
                message.chat.id,
                "Не удалось получить данные о давлении для этого региона.",
            )
            return

        # 4. Визуализация
        shape_path = "pogoda_regions.shp"
        if not os.path.exists(shape_path):
            bot.send_message(
                message.chat.id, "Ошибка: Файлы карты (.shp) не найдены на сервере."
            )
            return

        gdf_map = gpd.read_file(shape_path)
        geometry = [Point(xy) for xy in zip(list_long, list_lat)]
        gdf_points = gpd.GeoDataFrame(geometry=geometry, crs="EPSG:4326")

        # Цвета: красный если выше нормы, синий если ниже
        colors = ["red" if p > 1013.25 else "blue" for p in list_pressure]

        fig, ax = plt.subplots(figsize=(10, 8))
        # Отрисовка контура региона
        gdf_map[gdf_map["name_rus_5"] == region].plot(
            ax=ax, color="#DED4A3", edgecolor="black"
        )
        # Отрисовка точек
        gdf_points.plot(ax=ax, color=colors, markersize=40, edgecolors="white")

        # Подписи давления
        for i, val in enumerate(list_pressure):
            ax.annotate(val, (list_long[i], list_lat[i]), fontsize=7, fontweight="bold")

        plt.title(f"Атмосферное давление: {region}")
        plt.axis("off")

        # Сохранение и отправка
        map_img = f"map_{message.chat.id}.png"
        plt.savefig(map_img, dpi=150, bbox_inches="tight")
        plt.close()

        with open(map_img, "rb") as photo:
            bot.send_photo(
                message.chat.id,
                photo,
                caption=f"Готово! Карта давления для региона: {region}",
            )

        # Удаляем временный файл
        os.remove(map_img)

    except Exception as e:
        bot.send_message(message.chat.id, f"Произошла ошибка: {e}")

# --- ЗАПУСК ---

if __name__ == "__main__":
    print("Бот запущен и готов к работе...")
    bot.polling(non_stop=True)
