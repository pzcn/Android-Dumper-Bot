import os
import json
import sqlite3

# 初始化数据库连接
def init_db():
    conn = sqlite3.connect('file_cache.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS keyboard_layouts (
            file_name TEXT PRIMARY KEY,
            layout_data TEXT
        )
    ''')
    conn.commit()
    conn.close()

# 存储键盘布局到数据库
def store_keyboard_layout(file_name, layout_data):
    if not file_name.endswith(".zip"):
        file_name += ".zip"
    conn = sqlite3.connect('file_cache.db')
    cursor = conn.cursor()
    cursor.execute('INSERT OR REPLACE INTO keyboard_layouts (file_name, layout_data) VALUES (?, ?)', (file_name, json.dumps(layout_data)))
    conn.commit()
    conn.close()
    print(f"Stored keyboard layout for {file_name}")

# 创建分区键盘布局
def create_partition_keyboard(partitions_info):
    priority_partitions = ["boot", "init_boot", "vbmeta", "vbmeta_system"]
    partitions_info = sorted(
        partitions_info,
        key=lambda x: (x["partition_name"] not in priority_partitions, x["partition_name"]),
    )

    per_page_first = 12
    per_page_other = 14

    if len(partitions_info) <= per_page_first:
        total_pages = 1
    else:
        total_pages = ((len(partitions_info) - per_page_first) + per_page_other - 1) // per_page_other + 1

    pages = []
    page_number = 1
    start_index = 0
    while start_index < len(partitions_info):
        if page_number == 1:
            per_page = per_page_first
            start_index = 0
        else:
            per_page = per_page_other
            start_index = per_page_first + (page_number - 2) * per_page_other

        end_index = min(start_index + per_page, len(partitions_info))

        keyboard = []
        if page_number == 1:
            keyboard.append([{"text": "🏷️Fetch metadata", "callback_data": "metadata"}])

        row = []
        for i in range(start_index, end_index):
            p = partitions_info[i]
            row.append({"text": f"{p['partition_name']}({p['size_readable']})", "callback_data": f"{p['partition_name']}"})
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)

        prev_button = {"text": "⬅️", "callback_data": f"page {page_number - 1}"} if page_number > 1 else {"text": "⏹️", "callback_data": " "}
        next_button = {"text": "➡️", "callback_data": f"page {page_number + 1}"} if page_number < total_pages else {"text": "⏹️", "callback_data": " "}

        keyboard.append([prev_button, {"text": f"📄{page_number}/{total_pages}", "callback_data": " "}, next_button])

        pages.append({"page_number": page_number, "keyboard": keyboard})
        page_number += 1

    return {"file_name": None, "total_pages": total_pages, "pages": pages}

# 解析JSON文件并存储到数据库
def process_json_files(directory):
    for file_name in os.listdir(directory):
        if file_name.endswith(".json"):
            file_path = os.path.join(directory, file_name)
            with open(file_path, 'r') as f:
                partitions_info = json.load(f)
                layout_data = create_partition_keyboard(partitions_info)
                rom_file_name = os.path.splitext(file_name)[0] + ".zip"
                layout_data["file_name"] = rom_file_name
                store_keyboard_layout(rom_file_name, layout_data)

# 初始化数据库
init_db()

# 处理JSON文件并存储到数据库
process_json_files('output/partitions')

print("All JSON files have been processed and stored into the database.")
