import openai
import os
import re
import argparse
import curses
from curses import wrapper
from pathlib import Path
import textwrap
import locale
import sys
import json
import time
import datetime
import requests
import threading
import shutil

# 设置本地化以支持中文
locale.setlocale(locale.LC_ALL, '')
code = locale.getpreferredencoding()

# 最大文件大小限制（1MB）
MAX_FILE_SIZE = 1024 * 1024

# 最大消息长度限制（防止大输入导致崩溃）
MAX_MESSAGE_LENGTH = 5000

# 历史记录目录
HISTORY_DIR = Path("chat_history")
HISTORY_DIR.mkdir(exist_ok=True)

# 配置文件路径
CONFIG_FILE = "config.txt"

class ChatConfig:
    def __init__(self, name, api_base, api_key, model, request_type="openai", headers=None):
        self.name = name
        self.api_base = api_base
        self.api_key = api_key
        self.model = model
        self.request_type = request_type  # openai 或 curl
        self.headers = headers or {}
        self.provider = name  # 为了兼容原有代码
        self.is_infini = False  # 是否为Infini格式API
    
    def __str__(self):
        return f"{self.name} ({self.model})"
    
    def to_dict(self):
        return {
            "name": self.name,
            "api_base": self.api_base,
            "api_key": self.api_key,
            "model": self.model,
            "request_type": self.request_type,
            "headers": self.headers,
            "is_infini": self.is_infini
        }

def load_configurations():
    """从配置文件加载所有配置"""
    configs = []
    default_configs = [
        {
            "name": "OpenRouter",
            "api_base": "https://openrouter.ai/api/v1",
            "api_key": "API-KEY",
            "model": "deepseek/deepseek-r1:free",
            "request_type": "openai",
            "headers": {
                "HTTP-Referer": "https://github.com/YOU-NAME",
                "X-Title": "Termux Chat" # 或者其它
            },
            "is_infini": False
        }
    ]
    
    # 如果配置文件存在，则加载
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    # 跳过空行和注释
                    if not line or line.startswith('#'):
                        continue
                    
                    # 使用 :: 分割，最多分割6次，保留头部JSON完整
                    parts = line.split('::', 6)
                    
                    # 至少需要4部分
                    if len(parts) < 4:
                        continue
                    
                    # 创建配置对象
                    name = parts[0].strip()
                    api_base = parts[1].strip()
                    api_key = parts[2].strip()
                    model = parts[3].strip()
                    
                    # 请求类型 (默认为openai)
                    request_type = "openai"
                    if len(parts) > 4:
                        request_type = parts[4].strip().lower()
                        if request_type not in ["openai", "curl"]:
                            request_type = "openai"
                    
                    # 添加自定义头部（如果存在）
                    headers = {}
                    if len(parts) > 5:
                        headers_str = parts[5].strip()
                        if headers_str:
                            try:
                                # 尝试直接解析JSON
                                headers = json.loads(headers_str)
                            except json.JSONDecodeError:
                                # 尝试处理单引号或不标准JSON
                                try:
                                    headers_str = headers_str.replace("'", '"')
                                    headers = json.loads(headers_str)
                                except:
                                    print(f"警告: 无法解析头部JSON: {headers_str}")
                                    headers = {}
                            except Exception as e:
                                print(f"解析头部JSON出错: {str(e)}")
                                headers = {}
                    
                    # 是否为Infini格式API
                    is_infini = False
                    if len(parts) > 6:
                        infini_str = parts[6].strip().lower()
                        if infini_str == "infini" or infini_str == "true":
                            is_infini = True
                    
                    config = ChatConfig(name, api_base, api_key, model, request_type, headers)
                    config.is_infini = is_infini
                    configs.append(config)
        except Exception as e:
            print(f"加载配置文件出错: {str(e)}，使用默认配置")
            configs = [ChatConfig(**c) for c in default_configs]
    else:
        # 没有配置文件，使用默认配置
        configs = [ChatConfig(**c) for c in default_configs]
    
    return configs

def select_file_tui(stdscr, start_dir="."):
    """使用curses创建文件选择TUI"""
    # 保存当前curses状态
    original_cursor = curses.curs_set(1)
    stdscr.keypad(True)
    
    current_dir = Path(start_dir).resolve()
    selected_index = 0
    scroll_offset = 0
    
    while True:
        stdscr.clear()
        height, width = stdscr.getmaxyx()
        
        # 显示当前路径
        path_str = f"目录: {current_dir}"
        stdscr.addstr(0, 0, path_str[:width-1])
        
        # 获取目录内容
        try:
            entries = list(current_dir.iterdir())
            entries.sort(key=lambda x: (not x.is_dir(), x.name.lower()))
        except Exception as e:
            stdscr.addstr(1, 0, f"错误: {str(e)}")
            stdscr.refresh()
            stdscr.getch()
            # 恢复curses状态
            curses.curs_set(original_cursor)
            return None
        
        # 显示文件列表
        max_visible = height - 3
        visible_entries = entries[scroll_offset:scroll_offset+max_visible]
        
        for i, entry in enumerate(visible_entries):
            line = i + 1
            prefix = ">" if i + scroll_offset == selected_index else " "
            entry_type = "📁 " if entry.is_dir() else "📄 "
            display_name = f"{prefix} {entry_type}{entry.name}"
            
            if line < height:
                if i + scroll_offset == selected_index:
                    stdscr.attron(curses.A_REVERSE)
                    try:
                        stdscr.addstr(line, 0, display_name[:width-1])
                    except:
                        pass
                    stdscr.attroff(curses.A_REVERSE)
                else:
                    try:
                        stdscr.addstr(line, 0, display_name[:width-1])
                    except:
                        pass
        
        # 显示操作提示
        help_line = height - 1
        help_text = "Enter: 选择 | ↑↓: 移动 | ←: 上级目录 | Esc: 取消"
        try:
            stdscr.addstr(help_line, 0, help_text[:width-1])
        except:
            pass
        
        stdscr.refresh()
        key = stdscr.getch()
        
        if key == curses.KEY_UP and selected_index > 0:
            selected_index -= 1
            if selected_index < scroll_offset:
                scroll_offset = selected_index
        elif key == curses.KEY_DOWN and selected_index < len(entries) - 1:
            selected_index += 1
            if selected_index >= scroll_offset + max_visible:
                scroll_offset += 1
        elif key == curses.KEY_LEFT:
            # 返回上级目录
            if current_dir != current_dir.parent:
                current_dir = current_dir.parent
                entries = list(current_dir.iterdir())
                entries.sort(key=lambda x: (not x.is_dir(), x.name.lower()))
                selected_index = 0
                scroll_offset = 0
        elif key == curses.KEY_RIGHT or key == 10:  # Enter键
            selected_entry = entries[selected_index]
            if selected_entry.is_dir():
                current_dir = selected_entry
                selected_index = 0
                scroll_offset = 0
            else:
                # 恢复curses状态
                curses.curs_set(original_cursor)
                return str(selected_entry)
        elif key == 27:  # ESC键
            # 恢复curses状态
            curses.curs_set(original_cursor)
            return None

def select_provider_tui(stdscr, configs):
    """使用curses创建提供商选择TUI"""
    # 保存当前curses状态
    original_cursor = curses.curs_set(1)
    stdscr.keypad(True)
    
    selected_index = 0
    scroll_offset = 0
    
    while True:
        stdscr.clear()
        height, width = stdscr.getmaxyx()
        
        try:
            stdscr.addstr(0, 0, "选择API配置:")
        except:
            pass
        
        # 显示配置列表
        max_visible = height - 3
        visible_configs = configs[scroll_offset:scroll_offset+max_visible]
        
        for i, config in enumerate(visible_configs):
            line = i + 1
            prefix = ">" if i + scroll_offset == selected_index else " "
            infini_mark = " [Infini]" if config.is_infini else ""
            display_text = f"{prefix} {config.name} - {config.model} ({config.request_type}){infini_mark}"
            
            if line < height:
                if i + scroll_offset == selected_index:
                    stdscr.attron(curses.A_REVERSE)
                    try:
                        stdscr.addstr(line, 0, display_text[:width-1])
                    except:
                        pass
                    stdscr.attroff(curses.A_REVERSE)
                else:
                    try:
                        stdscr.addstr(line, 0, display_text[:width-1])
                    except:
                        pass
        
        # 显示滚动提示
        if scroll_offset > 0:
            try:
                stdscr.addstr(0, width-5, "↑")
            except:
                pass
        if scroll_offset + max_visible < len(configs):
            try:
                stdscr.addstr(height-1, width-5, "↓")
            except:
                pass
        
        help_text = "Enter: 选择 | ↑↓: 移动 | Esc: 取消"
        try:
            stdscr.addstr(height-1, 0, help_text[:width-1])
        except:
            pass
        
        stdscr.refresh()
        key = stdscr.getch()
        
        if key == curses.KEY_UP and selected_index > 0:
            selected_index -= 1
            if selected_index < scroll_offset:
                scroll_offset = selected_index
        elif key == curses.KEY_DOWN and selected_index < len(configs) - 1:
            selected_index += 1
            if selected_index >= scroll_offset + max_visible:
                scroll_offset += 1
        elif key == 10:  # Enter键
            # 恢复curses状态
            curses.curs_set(original_cursor)
            return configs[selected_index]
        elif key == 27:  # ESC键
            # 恢复curses状态
            curses.curs_set(original_cursor)
            return None

def replace_file_tags(input_str):
    """
    替换输入字符串中的 {{:F<文件名>}} 标记为文件内容
    """
    # 使用正则表达式查找所有 {{:F...}} 模式
    pattern = r'\{\{:F([^}]+)\}\}'
    matches = re.findall(pattern, input_str)
    
    for file_path in matches:
        file_path = file_path.strip()
        try:
            # 检查文件是否存在
            if not os.path.exists(file_path):
                input_str = input_str.replace(f"{{{{:F{file_path}}}}}", f"[文件不存在: {file_path}]")
                continue
            
            # 检查文件大小
            file_size = os.path.getsize(file_path)
            if file_size > MAX_FILE_SIZE:
                input_str = input_str.replace(f"{{{{:F{file_path}}}}}", f"[文件过大(>{MAX_FILE_SIZE/1024}KB): {file_path}]")
                continue
            
            # 读取文件内容
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # 替换标记
            input_str = input_str.replace(f"{{{{:F{file_path}}}}}", f"\n```文件内容:{file_path}\n{content}\n```\n")
        
        except Exception as e:
            input_str = input_str.replace(f"{{{{:F{file_path}}}}}", f"[读取文件出错: {str(e)}]")
    
    return input_str

def view_history_tui(stdscr):
    """查看历史记录的TUI界面"""
    # 保存当前curses状态
    original_cursor = curses.curs_set(1)
    stdscr.keypad(True)
    
    # 获取所有历史记录文件
    history_files = list(HISTORY_DIR.glob("*.json"))
    history_files.sort(key=os.path.getmtime, reverse=True)
    
    if not history_files:
        stdscr.addstr(0, 0, "没有历史记录")
        stdscr.refresh()
        stdscr.getch()
        curses.curs_set(original_cursor)
        return
    
    selected_index = 0
    scroll_offset = 0
    
    # 第一级：历史记录列表
    while True:
        stdscr.clear()
        height, width = stdscr.getmaxyx()
        
        # 显示标题
        title = "历史记录 (Enter查看详情, Esc返回)"
        stdscr.addstr(0, 0, title[:width-1])
        
        # 显示历史记录列表
        max_visible = height - 3
        visible_files = history_files[scroll_offset:scroll_offset+max_visible]
        
        for i, file_path in enumerate(visible_files):
            line = i + 1
            prefix = ">" if i + scroll_offset == selected_index else " "
            
            # 读取文件元数据
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    timestamp = data.get('timestamp', 0)
                    date_str = datetime.datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M')
                    title = data.get('title', "未命名对话")
                    first_message = ""
                    for msg in data['messages']:
                        if msg['role'] == 'user':
                            content = msg['content'].replace('\n', ' ')
                            if len(content) > 30:
                                content = content[:30] + "..."
                            first_message = f" | 用户: {content}"
                            break
                    
                    display_text = f"{prefix} {date_str} - {title}{first_message}"
            except:
                display_text = f"{prefix} {file_path.name}"
            
            if line < height:
                if i + scroll_offset == selected_index:
                    stdscr.attron(curses.A_REVERSE)
                    try:
                        stdscr.addstr(line, 0, display_text[:width-1])
                    except:
                        pass
                    stdscr.attroff(curses.A_REVERSE)
                else:
                    try:
                        stdscr.addstr(line, 0, display_text[:width-1])
                    except:
                        pass
        
        # 显示操作提示
        help_text = "↑↓: 移动 | Enter: 查看详情 | Esc: 返回"
        try:
            stdscr.addstr(height-1, 0, help_text[:width-1])
        except:
            pass
        
        stdscr.refresh()
        key = stdscr.getch()
        
        if key == curses.KEY_UP and selected_index > 0:
            selected_index -= 1
            if selected_index < scroll_offset:
                scroll_offset = selected_index
        elif key == curses.KEY_DOWN and selected_index < len(history_files) - 1:
            selected_index += 1
            if selected_index >= scroll_offset + max_visible:
                scroll_offset += 1
        elif key == 10:  # Enter键
            selected_file = history_files[selected_index]
            view_single_history(stdscr, selected_file)
            stdscr.clear()
        elif key == 27:  # ESC键
            break
    
    # 恢复curses状态
    curses.curs_set(original_cursor)

def view_single_history(stdscr, file_path):
    """查看单个历史记录的详细内容"""
    # 保存当前curses状态
    original_cursor = curses.curs_set(0)
    stdscr.keypad(True)
    
    # 初始化颜色（如果尚未初始化）
    if not curses.has_colors():
        curses.start_color()
    curses.init_pair(1, curses.COLOR_CYAN, curses.COLOR_BLACK)  # 用户消息
    curses.init_pair(2, curses.COLOR_YELLOW, curses.COLOR_BLACK)  # AI消息
    
    # 读取历史记录
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            messages = data.get('messages', [])
            title = data.get('title', "未命名对话")
            timestamp = data.get('timestamp', 0)
            date_str = datetime.datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M')
    except Exception as e:
        stdscr.addstr(0, 0, f"读取历史记录失败: {str(e)}")
        stdscr.refresh()
        stdscr.getch()
        curses.curs_set(original_cursor)
        return
    
    scroll_offset = 0
    height, width = stdscr.getmaxyx()
    
    # 第二级：历史记录详情
    while True:
        stdscr.clear()
        height, width = stdscr.getmaxyx()
        
        # 显示标题
        title_line = f"{title} - {date_str} (Esc返回)"
        stdscr.addstr(0, 0, title_line[:width-1])
        
        # 显示消息
        display_lines = []
        for msg in messages:
            role = msg["role"]
            content = msg["content"]
            
            # 根据角色设置颜色属性
            color_attr = curses.color_pair(1) if role == "user" else curses.color_pair(2)
            
            # 处理内容换行
            lines = content.split('\n')
            for line in lines:
                # 对长行进行换行处理
                wrapped = textwrap.wrap(line, width)
                for wline in wrapped:
                    # 存储行内容和颜色属性
                    display_lines.append((wline, color_attr))
        
        # 显示消息
        max_visible = height - 2
        visible_lines = display_lines[scroll_offset:scroll_offset+max_visible]
        
        for i, (line, color_attr) in enumerate(visible_lines):
            if i < height - 1:
                try:
                    stdscr.addstr(i+1, 0, line[:width-1], color_attr)
                except:
                    pass
        
        # 显示滚动提示
        if scroll_offset > 0:
            try:
                stdscr.addstr(0, width-5, "↑", curses.A_BOLD)
            except:
                pass
        if scroll_offset + max_visible < len(display_lines):
            try:
                stdscr.addstr(height-1, width-5, "↓", curses.A_BOLD)
            except:
                pass
        
        stdscr.refresh()
        key = stdscr.getch()
        
        if key == curses.KEY_UP and scroll_offset > 0:
            scroll_offset -= 1
        elif key == curses.KEY_DOWN and scroll_offset < len(display_lines) - max_visible:
            scroll_offset += 1
        elif key == 27:  # ESC键
            break
    
    # 恢复curses状态
    curses.curs_set(original_cursor)

class ChatUI:
    def __init__(self, stdscr, configs):
        self.stdscr = stdscr
        self.configs = configs
        self.current_config = configs[0]  # 默认使用第一个配置
        self.messages = []
        self.input_history = []
        self.history_index = -1
        self.current_input = ""
        self.cursor_pos = 0
        self.file_placeholders = {}  # 存储文件占位符信息
        self.last_redraw_time = 0
        self.redraw_throttle = 0.1  # 限制重绘频率（秒）
        self.dirty = False  # 标记是否需要重绘消息区域
        self.last_message_count = 0  # 记录上次消息数量
        self.cached_lines = []  # 缓存消息行
        
        # 命令模式相关属性
        self.command_mode = False
        self.command_input = ""
        self.command_cursor_pos = 0
        self.saved_input = ""  # 保存进入命令模式前的输入内容
        self.saved_cursor_pos = 0  # 保存进入命令模式前的光标位置
        
        # 初始化颜色
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_CYAN, -1)    # 标题
        curses.init_pair(2, curses.COLOR_YELLOW, -1)   # 用户输入
        curses.init_pair(3, curses.COLOR_BLUE, -1)  # AI输出
        curses.init_pair(4, curses.COLOR_RED, -1)     # 系统消息
        curses.init_pair(5, curses.COLOR_MAGENTA, -1)  # 文件内容
        curses.init_pair(6, curses.COLOR_RED, -1)      # 错误消息
        curses.init_pair(7, curses.COLOR_BLUE, -1)     # 历史记录标题
        
        # 设置窗口
        self.stdscr.keypad(True)
        curses.curs_set(1)
        self.height, self.width = self.stdscr.getmaxyx()
        
        # 启用UTF-8支持
        self.stdscr.encoding = 'utf-8'
        
    def safe_addstr(self, y, x, text, attr=None):
        """安全添加字符串，避免边界错误"""
        try:
            if attr:
                self.stdscr.attron(attr)
            # 尝试使用UTF-8编码输出
            if isinstance(text, str):
                text = text.encode('utf-8', 'ignore').decode('utf-8', 'ignore')
            self.stdscr.addstr(y, x, text[:self.width-1])
            if attr:
                self.stdscr.attroff(attr)
        except curses.error:
            pass
        except UnicodeEncodeError:
            # 如果编码失败，使用忽略错误的方式
            try:
                text = text.encode('utf-8', 'ignore').decode('utf-8', 'ignore')
                self.stdscr.addstr(y, x, text[:self.width-1])
            except:
                pass
    
    def display_header(self):
        """显示标题栏"""
        title = f"AI聊天助手 (提供商: {self.current_config.name}, 模型: {self.current_config.model.split('/')[-1]})"
        infini_mark = " [Infini]" if self.current_config.is_infini else ""
        self.safe_addstr(0, 0, title + infini_mark, curses.color_pair(1))
        
        # 分隔线
        try:
            self.stdscr.hline(1, 0, curses.ACS_HLINE, self.width)
        except:
            pass
        
    def display_messages(self):
        """显示聊天消息 - 修复版本，添加自动换行"""
        # 计算消息显示区域 - 从第2行开始（索引2对应第三行）
        start_line = 2  # 修改为从第三行开始显示消息
        end_line = self.height - 3
        max_lines = end_line - start_line
        
        # 清空消息区域
        for i in range(start_line, end_line):
            try:
                self.stdscr.move(i, 0)
                self.stdscr.clrtoeol()
            except:
                pass
        
        # 显示所有消息（不再使用缓存）
        display_lines = []
        for msg in self.messages:
            role = msg["role"]
            content = msg["content"]
            
            # 根据角色设置颜色
            if role == "user":
                prefix = "用户: "
                color = curses.color_pair(2)
            elif role == "assistant":
                prefix = "AI: "
                color = curses.color_pair(3)
            else:
                prefix = "系统: "
                color = curses.color_pair(4)
            
            # 处理文件内容标记
            if "```文件内容:" in content:
                parts = content.split("```文件内容:")
                for i, part in enumerate(parts):
                    if i > 0:
                        file_part = part.split("```", 1)
                        if len(file_part) > 1:
                            display_lines.append(("文件内容:" + file_part[0], curses.color_pair(5)))
                            display_lines.append((file_part[1], color))
                        else:
                            display_lines.append(("文件内容:" + file_part[0], curses.color_pair(5)))
                    else:
                        display_lines.append((part, color))
            else:
                # 在用户界面显示占位符而不是文件内容
                display_content = content
                for placeholder, file_path in self.file_placeholders.items():
                    if placeholder in display_content:
                        display_content = display_content.replace(placeholder, f"{{{{:F{file_path}}}}}")
                
                # 添加前缀
                display_content = prefix + display_content
                
                # 正确处理换行：先按原始换行符分割
                lines = display_content.split('\n')
                for line in lines:
                    # 对每一行进行自动换行处理
                    wrapped_lines = textwrap.wrap(line, self.width)
                    if wrapped_lines:
                        for wrapped_line in wrapped_lines:
                            display_lines.append((wrapped_line, color))
                    else:
                        # 空行
                        display_lines.append(("", color))
        
        # 显示消息（从底部向上）
        line_index = len(display_lines) - 1
        row = end_line - 1
        
        # 确保不会覆盖输入区域
        max_row = self.height - 4  # 输入区域上方留出空间
        
        while row >= start_line and line_index >= 0 and row <= max_row:
            line, color = display_lines[line_index]
            self.safe_addstr(row, 0, line, color)
            row -= 1
            line_index -= 1
    
    def display_input(self):
        """显示输入框"""
        # 输入区域分隔线
        try:
            # 清除可能的覆盖
            for i in range(self.height - 3, self.height):
                self.stdscr.move(i, 0)
                self.stdscr.clrtoeol()
            
            self.stdscr.hline(self.height - 3, 0, curses.ACS_HLINE, self.width)
        except:
            pass
        
        # 输入提示
        prompt = "> "
        
        if self.command_mode:
            # 命令模式下的显示
            self.safe_addstr(self.height - 2, 0, "命令: " + self.command_input)
            # 设置光标位置
            cursor_x = len("命令: ") + self.command_cursor_pos
            try:
                self.stdscr.move(self.height - 2, cursor_x)
            except:
                pass
        else:
            # 普通模式下的显示
            self.safe_addstr(self.height - 2, 0, prompt)
            
            # 显示输入内容
            max_input_width = self.width - len(prompt) - 1
            display_input = self.current_input
            
            # 如果输入内容过长，显示尾部部分
            if len(display_input) > max_input_width:
                start_idx = max(0, len(self.current_input) - max_input_width)
                display_input = self.current_input[start_idx:]
                if start_idx > 0:
                    display_input = "..." + display_input
            
            # 显示输入文本
            self.safe_addstr(self.height - 2, len(prompt), display_input)
            
            # 设置光标位置
            display_pos = max(0, self.cursor_pos - (len(self.current_input) - len(display_input)))
            if display_input.startswith("..."):
                display_pos += 3  # 跳过 "..." 前缀
                
            cursor_x = len(prompt) + min(len(display_input), display_pos)
            try:
                self.stdscr.move(self.height - 2, cursor_x)
            except:
                pass
    
    def display_help(self):
        """显示帮助信息"""
        help_text = "命令: Ctrl+L 输入命令 file=文件 provider=切换 clear=清除 exit=退出 save=保存 load=加载 history=查看历史"
        self.safe_addstr(self.height - 1, 0, help_text)
    
    def redraw(self, force=False):
        """重绘整个界面，带有限流"""
        current_time = time.time()
        if not force and current_time - self.last_redraw_time < self.redraw_throttle:
            return
            
        self.last_redraw_time = current_time
        self.stdscr.clear()
        self.display_header()
        self.display_messages()
        self.display_input()
        self.display_help()
        self.stdscr.refresh()
    
    def redraw_input_only(self):
        """仅重绘输入区域，提高性能"""
        # 清除输入区域
        try:
            self.stdscr.move(self.height - 3, 0)
            self.stdscr.clrtobot()
        except:
            pass
        
        # 重新绘制输入区域
        try:
            self.stdscr.hline(self.height - 3, 0, curses.ACS_HLINE, self.width)
        except:
            pass
        
        self.display_input()
        self.display_help()
        self.stdscr.refresh()
    
    def process_input(self, key):
        """处理用户输入 - 修复后台切换问题"""
        # 处理命令模式
        if self.command_mode:
            return self.process_command_input(key)
        
        # 处理控制键
        if key == curses.KEY_ENTER or key == 10 or key == 13:
            # 发送消息
            return self.send_message()
        
        elif key == 12:  # Ctrl+L 进入命令模式
            self.enter_command_mode()
            self.redraw_input_only()
            return False
        
        elif key == curses.KEY_UP:
            # 上一条历史记录
            if self.input_history:
                if self.history_index < len(self.input_history) - 1:
                    self.history_index += 1
                    self.current_input = self.input_history[self.history_index]
                    self.cursor_pos = len(self.current_input)
            self.redraw_input_only()
            return False
        
        elif key == curses.KEY_DOWN:
            # 下一条历史记录
            if self.input_history:
                if self.history_index > 0:
                    self.history_index -= 1
                    self.current_input = self.input_history[self.history_index]
                    self.cursor_pos = len(self.current_input)
                elif self.history_index == 0:
                    self.history_index = -1
                    self.current_input = ""
                    self.cursor_pos = 0
            self.redraw_input_only()
            return False
        
        elif key == curses.KEY_LEFT:
            # 向左移动光标
            if self.cursor_pos > 0:
                self.cursor_pos -= 1
            self.redraw_input_only()
            return False
        
        elif key == curses.KEY_RIGHT:
            # 向右移动光标
            if self.cursor_pos < len(self.current_input):
                self.cursor_pos += 1
            self.redraw_input_only()
            return False
        
        elif key == curses.KEY_BACKSPACE or key == 127:
            # 退格删除
            if self.cursor_pos > 0:
                # 删除单个字符
                self.current_input = self.current_input[:self.cursor_pos-1] + self.current_input[self.cursor_pos:]
                self.cursor_pos -= 1
            self.redraw_input_only()
            return False
        
        elif key == 27:  # ESC键
            return self.handle_command("exit")
        
        else:
            # 处理字符输入（包括中文）
            char = None
            
            # 修复：检查按键值是否在有效范围内
            if key < 0 or key > 0x10FFFF:  # Unicode最大码点
                # 无效的按键值，忽略
                return False
            
            # 处理多字节字符（如中文）
            if key > 127:
                # 收集可能的UTF-8字节序列
                bytes_seq = [key]
                self.stdscr.nodelay(True)  # 临时设置非阻塞模式
                
                # 尝试读取后续字节（最多2个，因为UTF-8最多4字节，但中文通常是3字节）
                for _ in range(2):
                    next_key = self.stdscr.getch()
                    # 修复：检查按键值是否有效
                    if next_key != -1 and 0 <= next_key <= 255:
                        bytes_seq.append(next_key)
                    else:
                        break
                
                self.stdscr.nodelay(False)  # 恢复阻塞模式
                
                # 将字节序列转换为字符串
                try:
                    byte_string = bytes(bytes_seq)
                    char = byte_string.decode('utf-8')
                except UnicodeDecodeError:
                    # 如果解码失败，只使用第一个字节
                    try:
                        char = chr(bytes_seq[0])
                    except:
                        char = None
                except ValueError:
                    # 处理无效字节值
                    char = None
            else:
                # ASCII字符
                try:
                    char = chr(key)
                except:
                    char = None
            
            if char:
                # 插入字符到当前位置
                self.current_input = self.current_input[:self.cursor_pos] + char + self.current_input[self.cursor_pos:]
                self.cursor_pos += len(char)
            
            # 只重绘输入区域
            self.redraw_input_only()
            return False
    
    def enter_command_mode(self):
        """进入命令模式"""
        self.command_mode = True
        self.saved_input = self.current_input
        self.saved_cursor_pos = self.cursor_pos
        self.current_input = ""
        self.cursor_pos = 0
        self.command_input = ""
        self.command_cursor_pos = 0
    
    def exit_command_mode(self, restore_input=True):
        """退出命令模式"""
        self.command_mode = False
        if restore_input:
            self.current_input = self.saved_input
            self.cursor_pos = self.saved_cursor_pos
        else:
            self.current_input = ""
            self.cursor_pos = 0
        self.saved_input = ""
        self.saved_cursor_pos = 0
    
    def process_command_input(self, key):
        """处理命令模式下的输入"""
        if key == curses.KEY_ENTER or key == 10 or key == 13:
            # 执行命令
            self.handle_command(self.command_input)
            self.exit_command_mode()
            return False
        
        elif key == 27:  # ESC键
            # 取消命令
            self.exit_command_mode()
            self.redraw_input_only()
            return False
        
        elif key == curses.KEY_BACKSPACE or key == 127:
            # 退格删除
            if self.command_cursor_pos > 0:
                # 删除单个字符
                self.command_input = self.command_input[:self.command_cursor_pos-1] + self.command_input[self.command_cursor_pos:]
                self.command_cursor_pos -= 1
            self.redraw_input_only()
            return False
        
        elif key == curses.KEY_LEFT:
            # 向左移动光标
            if self.command_cursor_pos > 0:
                self.command_cursor_pos -= 1
            self.redraw_input_only()
            return False
        
        elif key == curses.KEY_RIGHT:
            # 向右移动光标
            if self.command_cursor_pos < len(self.command_input):
                self.command_cursor_pos += 1
            self.redraw_input_only()
            return False
        
        else:
            # 处理字符输入（包括中文）
            char = None
            
            # 修复：检查按键值是否在有效范围内
            if key < 0 or key > 0x10FFFF:  # Unicode最大码点
                # 无效的按键值，忽略
                return False
            
            # 处理多字节字符（如中文）
            if key > 127:
                # 收集可能的UTF-8字节序列
                bytes_seq = [key]
                self.stdscr.nodelay(True)  # 临时设置非阻塞模式
                
                # 尝试读取后续字节（最多2个，因为UTF-8最多4字节，但中文通常是3字节）
                for _ in range(2):
                    next_key = self.stdscr.getch()
                    # 修复：检查按键值是否有效
                    if next_key != -1 and 0 <= next_key <= 255:
                        bytes_seq.append(next_key)
                    else:
                        break
                
                self.stdscr.nodelay(False)  # 恢复阻塞模式
                
                # 将字节序列转换为字符串
                try:
                    byte_string = bytes(bytes_seq)
                    char = byte_string.decode('utf-8')
                except UnicodeDecodeError:
                    # 如果解码失败，只使用第一个字节
                    try:
                        char = chr(bytes_seq[0])
                    except:
                        char = None
                except ValueError:
                    # 处理无效字节值
                    char = None
            else:
                # ASCII字符
                try:
                    char = chr(key)
                except:
                    char = None
            
            if char:
                # 插入字符到当前位置
                self.command_input = self.command_input[:self.command_cursor_pos] + char + self.command_input[self.command_cursor_pos:]
                self.command_cursor_pos += len(char)
            
            # 只重绘输入区域
            self.redraw_input_only()
            return False
    
    def handle_command(self, command=None):
        """处理命令"""
        if command.startswith('file') or command.startswith('f'):
            selected_file = select_file_tui(self.stdscr)
            if selected_file:
                # 使用唯一占位符避免自动展开
                placeholder = f"{{{{:F{selected_file}}}}}"
                
                # 如果当前输入不为空，在末尾添加空格和占位符
                if self.saved_input:
                    self.saved_input += " " + placeholder
                    self.saved_cursor_pos = len(self.saved_input)
                else:
                    # 如果输入为空，直接使用占位符
                    self.saved_input = placeholder
                    self.saved_cursor_pos = len(placeholder)
                
                # 存储占位符信息
                self.file_placeholders[placeholder] = selected_file
                
                # 重绘输入区域显示占位符
                self.redraw_input_only()
            return False
        
        elif command.startswith('provider') or command.startswith('p'):
            selected_config = select_provider_tui(self.stdscr, self.configs)
            if selected_config:
                self.current_config = selected_config
                self.add_system_message(f"已切换到: {selected_config.name} ({selected_config.model})")
            self.redraw(force=True)
            return False
        
        elif command.startswith('clear') or command.startswith('cr'):
            self.messages = []
            self.file_placeholders = {}  # 清除占位符
            self.add_system_message("对话历史已清除")
            self.redraw(force=True)
            return False
        
        elif command.startswith('save') or command.startswith('s'):
            # 获取文件名（如果有）
            parts = command.split(' ', 1)
            filename = parts[1] if len(parts) > 1 else None
            
            if not filename:
                # 生成默认文件名
                timestamp = int(time.time())
                filename = f"chat_{timestamp}.json"
            
            # 确保是JSON文件
            if not filename.endswith('.json'):
                filename += '.json'
            
            # 保存文件
            file_path = HISTORY_DIR / filename
            
            # 尝试获取对话标题（第一条用户消息）
            title = "未命名对话"
            for msg in self.messages:
                if msg['role'] == 'user':
                    title = msg['content'].replace('\n', ' ')[:20] + "..."
                    break
            
            # 在保存前恢复占位符格式
            messages_to_save = []
            for msg in self.messages:
                # 只保存用户和AI消息，跳过系统消息
                if msg['role'] == 'system':
                    continue
                    
                if msg['role'] == 'user':
                    content = msg['content']
                    for placeholder, file_path_val in self.file_placeholders.items():
                        if placeholder in content:
                            content = content.replace(placeholder, f"{{{{:F{file_path_val}}}}}")
                    messages_to_save.append({"role": msg['role'], "content": content})
                else:
                    messages_to_save.append(msg)
            
            data = {
                'timestamp': int(time.time()),
                'title': title,
                'provider': self.current_config.name,
                'model': self.current_config.model,
                'messages': messages_to_save
            }
            
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                self.add_system_message(f"对话已保存到: {file_path}")
            except Exception as e:
                self.add_system_message(f"保存失败: {str(e)}", is_error=True)
            self.redraw(force=True)
            return False
        
        elif command.startswith('load') or command.startswith('l'):
            # 获取文件名（如果有）
            parts = command.split(' ', 1)
            filename = parts[1] if len(parts) > 1 else None
            
            if filename:
                # 直接加载指定文件
                file_path = HISTORY_DIR / filename
                if not file_path.exists():
                    self.add_system_message(f"文件不存在: {file_path}", is_error=True)
                    return False
                
                self.load_history(file_path)
            else:
                # 显示文件选择界面
                file_path = select_file_tui(self.stdscr, str(HISTORY_DIR))
                if file_path:
                    self.load_history(Path(file_path))
            self.redraw(force=True)
            return False
        
        elif command.startswith('history') or command.startswith('h'):
            # 进入历史记录查看界面
            view_history_tui(self.stdscr)
            self.redraw(force=True)
            return False
        
        # 添加清理缓存命令
        elif command.startswith('clean') or command.startswith('cn'):
            # 确认操作
            self.add_system_message("确定要清理所有历史记录吗？(y/n)")
            self.redraw(force=True)
            
            # 等待用户确认
            key = self.stdscr.getch()
            if key == ord('y') or key == ord('Y'):
                try:
                    # 删除历史记录目录
                    if HISTORY_DIR.exists():
                        shutil.rmtree(HISTORY_DIR)
                    
                    # 重新创建目录
                    HISTORY_DIR.mkdir(exist_ok=True)
                    
                    self.add_system_message("所有历史记录已清理")
                except Exception as e:
                    self.add_system_message(f"清理失败: {str(e)}", is_error=True)
            else:
                self.add_system_message("清理操作已取消")
            
            self.redraw(force=True)
            return False
        
        elif command.startswith('exit') or command.startswith('quit'):
            return True
        
        # 处理未知命令
        self.add_system_message(f"未知命令: {command.split()[0] if ' ' in command else command}", is_error=True)
        return False
    
    def load_history(self, file_path):
        """加载历史记录"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # 只加载用户和AI消息，不加载系统消息
                self.messages = [msg for msg in data.get('messages', []) 
                                 if msg['role'] in ['user', 'assistant']]
                provider = data.get('provider', 'OpenRouter')
                model = data.get('model', 'deepseek/deepseek-r1:free')
                
                # 恢复配置
                found = False
                for config in self.configs:
                    if config.name == provider and config.model == model:
                        self.current_config = config
                        found = True
                        break
                
                if not found:
                    # 如果配置不存在，创建一个新的
                    self.current_config = ChatConfig(provider, "", "", model)
                
                self.add_system_message(f"已加载历史记录: {file_path.name}")
                self.add_system_message(f"提供商: {provider}, 模型: {model}")
                
                # 重建文件占位符
                self.file_placeholders = {}
                for msg in self.messages:
                    if msg['role'] == 'user':
                        content = msg['content']
                        matches = re.findall(r'\{\{:F([^}]+)\}\}', content)
                        for file_path in matches:
                            placeholder = f"{{{{:F{file_path}}}}}"
                            self.file_placeholders[placeholder] = file_path
        except Exception as e:
            self.add_system_message(f"加载失败: {str(e)}", is_error=True)
    
    def send_openai_request(self, messages_to_send):
        """使用OpenAI库发送请求"""
        try:
            # 流式请求
            response = openai.ChatCompletion.create(
                model=self.current_config.model,
                messages=messages_to_send,
                stream=True,
                api_base=self.current_config.api_base,
                api_key=self.current_config.api_key,
                headers=self.current_config.headers
            )
            
            full_response = ""
            for chunk in response:
                if 'choices' in chunk and len(chunk['choices']) > 0:
                    choice = chunk['choices'][0]
                    if 'delta' in choice and 'content' in choice['delta']:
                        content = choice['delta']['content']
                        full_response += content
                        
                        # 截断过长的响应
                        if len(full_response) > MAX_MESSAGE_LENGTH:
                            full_response = full_response[:MAX_MESSAGE_LENGTH] + "\n...（响应过长，已截断）"
                        
                        # 更新最后一条消息
                        self.messages[-1] = {"role": "assistant", "content": full_response}
                        
                        # 只重绘最后一条消息区域
                        self.update_last_message()
            
            if not full_response:
                self.add_system_message("<AI未返回有效响应>")
            
        except openai.error.APIError as e:
            self.add_system_message(f"API错误: {str(e)}", is_error=True)
        except Exception as e:
            self.add_system_message(f"发生错误: {str(e)}", is_error=True)
    
    def send_curl_request(self, messages_to_send):
        """使用Requests库发送自定义请求"""
        try:
            # 构建请求体 - 根据是否为Infini格式调整
            if self.current_config.is_infini:
                # Infini格式API使用不同的请求体结构
                payload = {
                    "model": self.current_config.model,
                    "messages": messages_to_send
                }
            else:
                payload = {
                    "model": self.current_config.model,
                    "messages": messages_to_send,
                    "stream": True
                }
            
            # 设置请求头
            headers = {
                "Authorization": f"Bearer {self.current_config.api_key}",
                "Content-Type": "application/json"
            }
            
            # 添加自定义头部
            if self.current_config.headers:
                headers.update(self.current_config.headers)
            
            # 发送请求
            response = requests.post(
                self.current_config.api_base,
                json=payload,
                headers=headers,
                stream=not self.current_config.is_infini  # Infini格式不使用流式
            )
            
            # 检查响应状态
            if response.status_code != 200:
                self.add_system_message(f"API错误: HTTP {response.status_code} - {response.text}", is_error=True)
                return
            
            # 处理Infini格式的非流式响应
            if self.current_config.is_infini:
                try:
                    data = response.json()
                    if "choices" in data and len(data["choices"]) > 0:
                        choice = data["choices"][0]
                        if "message" in choice and "content" in choice["message"]:
                            full_response = choice["message"]["content"]
                            self.messages[-1] = {"role": "assistant", "content": full_response}
                            self.update_last_message()
                        else:
                            self.add_system_message("API响应格式不兼容", is_error=True)
                    else:
                        self.add_system_message("<AI未返回有效响应>")
                except Exception as e:
                    self.add_system_message(f"解析响应出错: {str(e)}", is_error=True)
                return
            
            # 处理流式响应
            full_response = ""
            for line in response.iter_lines():
                # 过滤心跳包
                if not line:
                    continue
                
                # 尝试解析JSON
                try:
                    # 移除 "data: " 前缀 (如果存在)
                    line_str = line.decode('utf-8')
                    if line_str.startswith("data: "):
                        line_str = line_str[6:]
                    
                    data = json.loads(line_str)
                    
                    # 检查是否有内容
                    if "choices" in data and len(data["choices"]) > 0:
                        choice = data["choices"][0]
                        if "delta" in choice and "content" in choice["delta"]:
                            content = choice["delta"]["content"]
                            full_response += content
                            
                            # 截断过长的响应
                            if len(full_response) > MAX_MESSAGE_LENGTH:
                                full_response = full_response[:MAX_MESSAGE_LENGTH] + "\n...（响应过长，已截断）"
                            
                            # 更新最后一条消息
                            self.messages[-1] = {"role": "assistant", "content": full_response}
                            
                            # 只重绘最后一条消息区域
                            self.update_last_message()
                    
                    # 检查是否结束
                    if data.get("done", False) or data.get("finish_reason", None):
                        break
                    
                except json.JSONDecodeError:
                    # 忽略非JSON行
                    pass
                except Exception as e:
                    self.add_system_message(f"解析错误: {str(e)}", is_error=True)
                    break
            
            if not full_response:
                self.add_system_message("<AI未返回有效响应>")
            
        except requests.exceptions.RequestException as e:
            self.add_system_message(f"网络错误: {str(e)}", is_error=True)
        except Exception as e:
            self.add_system_message(f"发生错误: {str(e)}", is_error=True)
    
    def send_message(self):
        """发送消息给AI"""
        if not self.current_input.strip():
            return False
        
        # 保存到历史记录
        self.input_history.insert(0, self.current_input)
        self.history_index = -1
        
        # 处理文件标记 - 只在发送给AI时展开
        processed_input = self.current_input
        for placeholder, file_path in self.file_placeholders.items():
            if placeholder in processed_input:
                try:
                    # 检查文件是否存在
                    if not os.path.exists(file_path):
                        processed_input = processed_input.replace(placeholder, f"[文件不存在: {file_path}]")
                        continue
                    
                    # 检查文件大小
                    file_size = os.path.getsize(file_path)
                    if file_size > MAX_FILE_SIZE:
                        processed_input = processed_input.replace(placeholder, f"[文件过大(>{MAX_FILE_SIZE/1024}KB): {file_path}]")
                        continue
                    
                    # 读取文件内容
                    with open(file_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                    
                    # 替换标记
                    processed_input = processed_input.replace(placeholder, f"\n```文件内容:{file_path}\n{content}\n```\n")
                
                except Exception as e:
                    processed_input = processed_input.replace(placeholder, f"[读取文件出错: {str(e)}]")
        
        # 截断过长的消息
        if len(processed_input) > MAX_MESSAGE_LENGTH:
            processed_input = processed_input[:MAX_MESSAGE_LENGTH] + "\n...（消息过长，已截断）"
        
        # 添加用户消息
        self.messages.append({"role": "user", "content": self.current_input})
        self.dirty = True  # 标记需要重绘消息区域
        self.redraw(force=True)
        
        # 构建要发送的消息列表（排除系统消息）
        messages_to_send = []
        for msg in self.messages:
            # 只发送用户和AI消息，不发送系统消息
            if msg["role"] == "system":
                continue
                
            if msg["role"] == "user":
                # 对用户消息中的文件占位符进行替换
                content = replace_file_tags(msg["content"])
            else:
                content = msg["content"]
                
            # 只保留最近的10条消息
            if len(messages_to_send) < 10:
                messages_to_send.append({"role": msg["role"], "content": content})
            else:
                # 移除最旧的消息
                messages_to_send.pop(0)
                messages_to_send.append({"role": msg["role"], "content": content})
        
        # 添加AI消息占位符
        self.messages.append({"role": "assistant", "content": "正在思考..."})
        self.dirty = True
        self.redraw(force=True)
        
        # 根据配置选择请求方式
        if self.current_config.request_type == "curl":
            # 使用线程发送请求，避免阻塞UI
            threading.Thread(
                target=self.send_curl_request,
                args=(messages_to_send,),
                daemon=True
            ).start()
        else:  # 默认为openai方式
            # 使用线程发送请求，避免阻塞UI
            threading.Thread(
                target=self.send_openai_request,
                args=(messages_to_send,),
                daemon=True
            ).start()
        
        # 清空输入
        self.current_input = ""
        self.cursor_pos = 0
        self.redraw_input_only()
        return False
    
    def update_last_message(self):
        """只更新最后一条消息的显示 - 修复版本，添加自动换行"""
        # 计算消息显示区域
        start_line = 2  # 从第三行开始
        end_line = self.height - 3
        
        # 清除最后两条消息的区域
        # 计算需要清除的行数（基于最后两条消息的实际行数）
        lines_to_clear = 0
        for msg in self.messages[-2:]:
            # 跳过系统消息
            if msg["role"] == "system":
                continue
                
            content = msg["content"]
            # 估算行数：内容长度除以终端宽度，加上换行符
            lines_to_clear += max(1, (len(content) // self.width) + 1)
        
        # 确保不会清除到标题区域
        clear_start = max(start_line, end_line - lines_to_clear - 2)
        
        for i in range(clear_start, end_line):
            try:
                self.stdscr.move(i, 0)
                self.stdscr.clrtoeol()
            except:
                pass
        
        # 只显示最后两条消息（排除系统消息）
        display_lines = []
        for msg in self.messages[-2:]:
            # 跳过系统消息
            if msg["role"] == "system":
                continue
                
            role = msg["role"]
            content = msg["content"]
            
            if role == "user":
                prefix = "用户: "
                color = curses.color_pair(2)
            elif role == "assistant":
                prefix = "AI: "
                color = curses.color_pair(3)
            else:
                prefix = "系统: "
                color = curses.color_pair(4)
            
            # 处理文件内容标记
            if "```文件内容:" in content:
                parts = content.split("```文件内容:")
                for i, part in enumerate(parts):
                    if i > 0:
                        file_part = part.split("```", 1)
                        if len(file_part) > 1:
                            display_lines.append(("文件内容:" + file_part[0], curses.color_pair(5)))
                            display_lines.append((file_part[1], color))
                        else:
                            display_lines.append(("文件内容:" + file_part[0], curses.color_pair(5)))
                    else:
                        display_lines.append((part, color))
            else:
                # 在用户界面显示占位符而不是文件内容
                display_content = content
                for placeholder, file_path in self.file_placeholders.items():
                    if placeholder in display_content:
                        display_content = display_content.replace(placeholder, f"{{{{:F{file_path}}}}}")
                
                # 添加前缀
                display_content = prefix + display_content
                
                # 正确处理换行：先按原始换行符分割
                lines = display_content.split('\n')
                for line in lines:
                    # 对每一行进行自动换行处理
                    wrapped_lines = textwrap.wrap(line, self.width)
                    if wrapped_lines:
                        for wrapped_line in wrapped_lines:
                            display_lines.append((wrapped_line, color))
                    else:
                        # 空行
                        display_lines.append(("", color))
        
        # 显示消息（从底部向上）
        line_index = len(display_lines) - 1
        row = end_line - 1
        
        # 确保不会覆盖输入区域
        max_row = self.height - 4
        
        while row >= max_row and line_index >= 0:
            line, color = display_lines[line_index]
            self.safe_addstr(row, 0, line, color)
            row -= 1
            line_index -= 1
        
        # 重新显示输入区域（确保位置正确）
        try:
            # 清除分隔线和输入行
            self.stdscr.move(self.height - 3, 0)
            self.stdscr.clrtoeol()
            self.stdscr.hline(self.height - 3, 0, curses.ACS_HLINE, self.width)
            
            self.stdscr.move(self.height - 2, 0)
            self.stdscr.clrtoeol()
            
            self.stdscr.move(self.height - 1, 0)
            self.stdscr.clrtoeol()
        except:
            pass
        
        self.display_input()
        self.display_help()
        self.stdscr.refresh()
    
    def add_system_message(self, message, is_error=False):
        """添加系统消息"""
        color = curses.color_pair(6) if is_error else curses.color_pair(4)
        # 截断过长的系统消息
        if len(message) > MAX_MESSAGE_LENGTH:
            message = message[:MAX_MESSAGE_LENGTH] + " ...（消息过长，已截断）"
            
        self.messages.append({
            "role": "system", 
            "content": message
        })
        self.dirty = True  # 标记需要重绘消息区域
        self.redraw(force=True)

def chat_ui(stdscr, configs):
    # 设置UTF-8支持
    stdscr.keypad(True)
    curses.curs_set(1)
    curses.noecho()
    curses.cbreak()
    
    # 设置编码
    sys.stdout.reconfigure(encoding='utf-8')
    
    # 创建UI实例
    ui = ChatUI(stdscr, configs)
    
    # 初始系统消息
    ui.add_system_message("提示：在消息中使用 {{:F文件名}} 自动插入文件内容")
    ui.add_system_message("输入 file 选择文件，provider 切换API，clear 清除历史，exit 退出")
    ui.add_system_message("输入 save 保存对话，load 加载对话，history 查看历史记录")
    
    # 主循环
    while True:
        ui.redraw()
        key = stdscr.getch()
        exit_flag = ui.process_input(key)
        if exit_flag:
            break

def chat(configs):
    """
     curses.wrapper 初始化终端环境。
     将初始化后的 stdscr 传递给 lambda 函数。
     Lambda 函数调用 chat_ui(stdscr, configs)，启动聊天界面逻辑。
     当 chat_ui 执行完毕（或抛出异常），wrapper 自动清理终端状态。
     """
    wrapper(lambda stdscr: chat_ui(stdscr, configs))

def create_default_config():
    """创建默认配置文件"""
    config_content = """# API配置格式: 名称::API地址::API密钥::模型::请求类型(openai/curl)::头部(JSON格式，可选)::是否为Infini格式(可选)
# 请求类型: openai (兼容OpenAI) 或 curl (自定义请求)"""
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        f.write(config_content)
    print(f"已创建默认配置文件: {CONFIG_FILE}")
    print("请编辑该文件添加您的API配置")

if __name__ == "__main__":
    # 设置本地化以支持中文
    locale.setlocale(locale.LC_ALL, '')
    
    # 设置标准输出编码
    sys.stdout.reconfigure(encoding='utf-8')
    
    # 如果配置文件不存在，创建默认配置
    if not os.path.exists(CONFIG_FILE):
        create_default_config()
    
    # 加载配置
    configs = load_configurations()
    
    # 解析命令行参数
    parser = argparse.ArgumentParser(description='AI聊天客户端')
    parser.add_argument('--provider', help='指定要使用的API名称')
    parser.add_argument('--model', help='指定要使用的模型')
    args = parser.parse_args()

    # 根据命令行参数选择配置
    selected_config = None
    
    if args.provider or args.model:
        # 尝试匹配命令行参数
        for config in configs:
            if args.provider and config.name.lower() != args.provider.lower():
                continue
            if args.model and config.model.lower() != args.model.lower():
                continue
            selected_config = config
            break
        
        if not selected_config:
            print(f"未找到匹配的配置: 提供商={args.provider}, 模型={args.model}")
            print("使用默认配置")
            selected_config = configs[0]
    else:
        selected_config = configs[0]
    
    # 设置当前配置为选择的配置
    configs.insert(0, selected_config)
    
    # 启动聊天
    try:
        chat(configs)
    except Exception as e:
        print(f"程序发生错误: {str(e)}")
        import traceback
        traceback.print_exc()