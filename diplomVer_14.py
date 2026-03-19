import customtkinter as ctk
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import subprocess
import tempfile
import jedi
import json
import sys
import os
import threading
import re
from perplexipy import PerplexityClient, PerplexityClientError
# Проверяем доступность Jedi
try:
    import jedi
    JEDI_AVAILABLE = True
except ImportError:
    JEDI_AVAILABLE = False
    print("Jedi не установлен. Автодополнение недоступно.")


# Настройка CustomTkinter
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("tema.json")

# API ключ Perplexity
key = os.environ["PERPLEXITY_API_KEY"] = "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"
client = PerplexityClient(key=key)

def entry_copy(event):
    widget = event.widget
    if isinstance(widget, ctk.CTkEntry):
        try:
            widget.clipboard_clear()
            widget.clipboard_append(widget.get())
        except Exception:
            pass

def entry_paste(event):
    widget = event.widget
    if isinstance(widget, ctk.CTkEntry):
        try:
            widget.insert(ctk.END, widget.clipboard_get())
        except Exception:
            pass

def entry_cut(event):
    widget = event.widget
    if isinstance(widget, ctk.CTkEntry):
        try:
            widget.clipboard_clear()
            widget.clipboard_append(widget.get())
            widget.delete(0, ctk.END)
        except Exception:
            pass

class AutocompletePopup(ctk.CTkToplevel):
    def __init__(self, parent, text_widget, completions, x, y):
        super().__init__(parent)
        self.text_widget = text_widget
        self.completions = completions
        self.selected_index = 0
        
        self.withdraw()
        self.overrideredirect(True)
        self.configure(fg_color="#2F3F4F")
        
        # Создаем список автодополнений
        self.listbox = tk.Listbox(self, 
                                 bg="#2F3F4F", 
                                 fg="#FFD600",
                                 selectbackground="#FFD600",
                                 selectforeground="#000000",
                                 height=min(10, len(completions)))
        self.listbox.pack(fill='both', expand=True)
        
        for completion in completions:
            self.listbox.insert('end', completion.name)
        
        if completions:
            self.listbox.selection_set(0)
        
        # Позиционируем окно
        self.geometry(f"+{x}+{y}")
        self.deiconify()
        
        # Привязываем события
        self.listbox.bind('<Double-Button-1>', self.insert_completion)
        self.listbox.bind('<Return>', self.insert_completion)
        self.bind('<Escape>', lambda e: self.destroy())
        
    def insert_completion(self, event=None):
        selection = self.listbox.curselection()
        if selection:
            completion = self.completions[selection[0]]
            # Вставляем автодополнение
            current_pos = self.text_widget.index(tk.INSERT)
            line_start = f"{current_pos.split('.')[0]}.0"
            line_text = self.text_widget.get(line_start, current_pos)
            
            # Находим начало текущего слова
            word_start = len(line_text)
            for i in range(len(line_text) - 1, -1, -1):
                if not (line_text[i].isalnum() or line_text[i] == '_'):
                    break
                word_start = i
            
            # Удаляем частичное слово и вставляем полное
            delete_start = f"{current_pos.split('.')[0]}.{word_start}"
            self.text_widget.delete(delete_start, current_pos)
            self.text_widget.insert(delete_start, completion.name)
        
        self.destroy()


# Диалог для ввода данных
class UserInputDialog(ctk.CTkToplevel):
    def __init__(self, parent, prompt):
        super().__init__(parent)
        self.title("Ввод данных")
        self.geometry("400x150")
        self.resizable(False, False)
        self.entry_var = tk.StringVar()
        
        ctk.CTkLabel(self, text=prompt).pack(pady=10)
        entry = ctk.CTkEntry(self, textvariable=self.entry_var)
        entry.pack(pady=5, padx=20, fill='x')
        entry.bind('<Return>', lambda e: self.ok())
        
        btn_frame = ctk.CTkFrame(self)
        btn_frame.pack(pady=5)
        ctk.CTkButton(btn_frame, text="OK", command=self.ok).pack(side='left', padx=10)
        ctk.CTkButton(btn_frame, text="Отмена", command=self.cancel).pack(side='right', padx=10)
        
        self.result = None

    def ok(self):
        self.result = self.entry_var.get()
        self.destroy()

    def cancel(self):
        self.result = None
        self.destroy()


class TextLineNumbers(tk.Canvas):
    def __init__(self, master, text_widget, **kwargs):
        super().__init__(master, bg='#232b36', highlightthickness=0, **kwargs)
        self.text_widget = text_widget
        self.line_number_objects = []
        self.folded_regions = {}
        self.fold_icons = {}
        self.foldable_regions = []
        self.fold_active = False
        
        # Привязываем события для синхронизации
        self.text_widget.bind("<KeyRelease>", self.redraw)
        self.text_widget.bind("<MouseWheel>", self.redraw)
        self.text_widget.bind("<Button-4>", self.redraw)
        self.text_widget.bind("<Button-5>", self.redraw)
        self.text_widget.bind("<Configure>", self.redraw)
        
        # Обработка кликов по маркерам
        self.bind('<Button-1>', self.on_gutter_click)
        
        # Привязываем скролл
        old_yview = self.text_widget.yview
        def new_yview(*args):
            result = old_yview(*args)
            self.redraw()
            return result
        self.text_widget.yview = new_yview

    def redraw(self, event=None):
        try:
            # Удаляем только номера строк
            for obj_id in self.line_number_objects:
                try:
                    self.delete(obj_id)
                except:
                    pass
            self.line_number_objects.clear()
            
            # Удаляем только маркеры (если активны)
            if self.fold_active:
                for line, data in list(self.fold_icons.items()):
                    try:
                        self.delete(data['icon_id'])
                    except:
                        pass
                self.fold_icons.clear()
            
            # ИСПРАВЛЕНИЕ: Получаем текущий цвет текста из темы
            try:
                # Пытаемся получить цвет из color_vars, если они доступны
                if hasattr(self.text_widget.master.master.master.master, 'color_vars'):
                    text_color = self.text_widget.master.master.master.master.color_vars["text_color"].get()
                else:
                    text_color = "#FFD600"  # Цвет по умолчанию
            except:
                text_color = "#FFD600"  # Fallback цвет
            
            # Рисуем номера строк только для видимых строк
            i = self.text_widget.index("@0,0")
            
            while True:
                dline = self.text_widget.dlineinfo(i)
                if dline is None:
                    break
                
                # Проверяем, скрыта ли строка тегом "folded"
                line_num = int(i.split(".")[0])
                is_folded = self._is_line_folded(line_num)
                
                if not is_folded:  # Показываем номер только для видимых строк
                    y = dline[1]
                    # ИСПРАВЛЕНИЕ: Используем динамический цвет вместо жестко заданного
                    obj_id = self.create_text(45, y, anchor="ne", text=str(line_num), 
                                            fill=text_color, font=('Consolas', 12))
                    self.line_number_objects.append(obj_id)
                
                i = self.text_widget.index(f"{i}+1line")
            
            # Рисуем маркеры сворачивания (если активны)
            if self.fold_active:
                self.draw_fold_markers()
                
        except:
            pass


    def _is_line_folded(self, line_num):
        """Проверяет, скрыта ли строка тегом folded"""
        try:
            # Получаем все диапазоны тега "folded"
            folded_ranges = self.text_widget.tag_ranges("folded")
            
            for i in range(0, len(folded_ranges), 2):
                start_pos = folded_ranges[i]
                end_pos = folded_ranges[i + 1]
                
                start_line = int(str(start_pos).split('.')[0])
                end_line = int(str(end_pos).split('.')[0])
                
                # Проверяем, попадает ли наша строка в скрытый диапазон
                if start_line <= line_num <= end_line:
                    return True
            
            return False
        except:
            return False


    def draw_fold_markers(self):
        """Рисует маркеры сворачивания"""
        for start_line, end_line in self.foldable_regions:
            try:
                dline = self.text_widget.dlineinfo(f"{start_line}.0")
                if dline:
                    y = dline[1] + dline[3] // 2
                    is_folded = start_line in self.folded_regions
                    
                    if is_folded:
                        # Треугольник "вправо" (свёрнуто)
                        icon_id = self.create_polygon(
                            13, y-4, 17, y, 13, y+4,
                            fill="#FFD600", outline="#FFD600", width=1
                        )
                    else:
                        # Треугольник "вниз" (развёрнуто)
                        icon_id = self.create_polygon(
                            15, y-4, 19, y, 15, y+4,
                            fill="#FFD600", outline="#FFD600", width=1
                        )
                    
                    self.fold_icons[start_line] = {
                        'icon_id': icon_id,
                        'end_line': end_line,
                        'folded': is_folded
                    }
            except:
                pass

    def find_foldable_regions(self):
        """Находит области, которые можно свернуть"""
        try:
            content = self.text_widget.get('1.0', 'end-1c')
            lines = content.split('\n')
            regions = []
            
            i = 0
            while i < len(lines):
                line = lines[i].strip()
                
                if (line.startswith('def ') or line.startswith('class ')) and line.endswith(':'):
                    start_line = i + 1
                    indent_level = len(lines[i]) - len(lines[i].lstrip())
                    end_line = start_line
                    
                    for j in range(i + 1, len(lines)):
                        if lines[j].strip() == '':
                            continue
                        current_indent = len(lines[j]) - len(lines[j].lstrip())
                        if current_indent <= indent_level and lines[j].strip() != '':
                            break
                        end_line = j + 1
                    
                    if end_line > start_line + 1:
                        regions.append((start_line, end_line))
                        
                i += 1
            
            return regions
        except:
            return []

    def show_fold_markers(self):
        """Показывает маркеры сворачивания"""
        self.fold_active = True
        self.foldable_regions = self.find_foldable_regions()
        self.redraw()

    def hide_fold_markers(self):
        """Скрывает маркеры сворачивания и разворачивает все области"""
        # Разворачиваем все свернутые области
        for line in list(self.folded_regions.keys()):
            self.unfold_region(line)
        
        self.fold_active = False
        self.foldable_regions = []
        self.redraw()

    def on_gutter_click(self, event):
        """Обработка клика по области номеров строк"""
        if not self.fold_active:
            return
            
        for line, data in self.fold_icons.items():
            try:
                dline = self.text_widget.dlineinfo(f"{line}.0")
                if dline and abs(event.y - (dline[1] + dline[3] // 2)) < 10:
                    if data['folded']:
                        self.unfold_region(line)
                    else:
                        self.fold_region(line)
                    break
            except:
                pass

    def fold_region(self, start_line):
        """Сворачивает область"""
        try:
            if start_line not in self.fold_icons:
                return
                
            data = self.fold_icons[start_line]
            end_line = data['end_line']
            
            # Скрываем строки
            for line in range(start_line + 1, end_line + 1):
                self.text_widget.tag_add("folded", f"{line}.0", f"{line}.end+1c")
            
            self.text_widget.tag_configure("folded", elide=True)
            self.folded_regions[start_line] = end_line
            
            # Обновляем маркеры И номера строк
            self.update_markers()
            self._update_line_numbers()
        except:
            pass

    def unfold_region(self, start_line):
        """Разворачивает область"""
        try:
            if start_line not in self.folded_regions:
                return
                
            end_line = self.folded_regions[start_line]
            self.text_widget.tag_remove("folded", f"{start_line+1}.0", f"{end_line+1}.0")
            del self.folded_regions[start_line]
            
            # Обновляем маркеры И номера строк
            self.update_markers()
            self._update_line_numbers()
        except:
            pass

    def _update_line_numbers(self):
        """Обновляет номера строк"""
        try:
            # Находим объект номеров строк через родительские элементы
            parent = self.text_widget.master
            for child in parent.winfo_children():
                if hasattr(child, 'redraw') and hasattr(child, 'line_number_objects'):
                    child.redraw()
                    break
        except:
            pass



class ModernCodeFolding:
    def init(self, text_widget, line_numbers_canvas):
        self.text_widget = text_widget
        self.line_numbers = line_numbers_canvas
        self.folded_regions = {}
        self.fold_icons = {}  # {line: icon_id}
        
        # Добавляем обработчик клика на Canvas номеров строк
        self.line_numbers.bind('<Button-1>', self.on_gutter_click)
        
    def add_fold_icons(self):
        """Добавляет современные треугольные иконки сворачивания"""
        regions = self.find_foldable_regions()
        
        for start_line, end_line in regions:
            # Вычисляем позицию иконки
            try:
                dline = self.text_widget.dlineinfo(f"{start_line}.0")
                if dline:
                    y = dline[1] + dline[3] // 2  # Центр строки
                    
                    # Рисуем треугольник "вниз" (развёрнуто)
                    icon_id = self.line_numbers.create_polygon(
                        15, y-4,  # Верх треугольника
                        19, y,    # Правый угол
                        15, y+4,  # Левый угол
                        fill="#FFD600", outline="#FFD600", width=1
                    )
                    
                    self.fold_icons[start_line] = {
                        'icon_id': icon_id,
                        'end_line': end_line,
                        'folded': False
                    }
            except:
                pass
    
    def on_gutter_click(self, event):
        """Обработка клика по области номеров строк"""
        # Определяем строку по Y-координате
        for line, data in self.fold_icons.items():
            try:
                dline = self.text_widget.dlineinfo(f"{line}.0")
                if dline and abs(event.y - (dline[1] + dline[3] // 2)) < 10:
                    if data['folded']:
                        self.unfold_region(line)
                    else:
                        self.fold_region(line)
                    break
            except:
                pass
    
    def fold_region(self, start_line):
        """Современное сворачивание через elide"""
        if start_line not in self.fold_icons:
            return
            
        data = self.fold_icons[start_line]
        end_line = data['end_line']
        
        # Скрываем строки через elide (без изменения текста!)
        for line in range(start_line + 1, end_line + 1):
            self.text_widget.tag_add("folded", f"{line}.0", f"{line}.end+1c")
        
        self.text_widget.tag_configure("folded", elide=True)
        
        # Меняем иконку на треугольник "вправо"
        icon_id = data['icon_id']
        self.line_numbers.delete(icon_id)
        
        dline = self.text_widget.dlineinfo(f"{start_line}.0")
        if dline:
            y = dline[1] + dline[3] // 2
            new_icon = self.line_numbers.create_polygon(
                13, y-4,  # Левый угол
                17, y,    # Правый угол (стрелка)
                13, y+4,  # Левый угол
                fill="#FFD600", outline="#FFD600", width=1
            )
            data['icon_id'] = new_icon
            data['folded'] = True
    
    def unfold_region(self, start_line):
        """Разворачивание области"""
        if start_line not in self.fold_icons:
            return
            
        data = self.fold_icons[start_line]
        end_line = data['end_line']
        
        # Показываем строки
        self.text_widget.tag_remove("folded", f"{start_line+1}.0", f"{end_line+1}.0")
        
        # Меняем иконку обратно на треугольник "вниз"
        icon_id = data['icon_id']
        self.line_numbers.delete(icon_id)
        
        dline = self.text_widget.dlineinfo(f"{start_line}.0")
        if dline:
            y = dline[1] + dline[3] // 2
            new_icon = self.line_numbers.create_polygon(
                15, y-4, 19, y, 15, y+4,
                fill="#FFD600", outline="#FFD600", width=1
            )
            data['icon_id'] = new_icon
            data['folded'] = False

class FoldingMarkers(tk.Canvas):
    def __init__(self, master, text_widget, **kwargs):
        super().__init__(master, bg='#232b36', highlightthickness=0, width=20, **kwargs)
        self.text_widget = text_widget
        self.folded_regions = {}
        self.fold_icons = {}
        self.foldable_regions = []
        self.active = False
        
        # Обработка кликов по маркерам
        self.bind('<Button-1>', self.on_click)
        
        # Привязываем обновление к прокрутке текста
        self.text_widget.bind("<KeyRelease>", self.update_markers)
        self.text_widget.bind("<MouseWheel>", self.update_markers)
        self.text_widget.bind("<Button-4>", self.update_markers)
        self.text_widget.bind("<Button-5>", self.update_markers)
        self.text_widget.bind("<Configure>", self.update_markers)
        
        # Перехватываем yview
        old_yview = self.text_widget.yview
        def new_yview(*args):
            result = old_yview(*args)
            if self.active:
                self.update_markers()
            return result
        self.text_widget.yview = new_yview

    def show_markers(self):
        """Показывает маркеры сворачивания"""
        self.active = True
        self.foldable_regions = self.find_foldable_regions()
        self.update_markers()

    def hide_markers(self):
        """Скрывает маркеры и разворачивает код"""
        # Разворачиваем все области
        for line in list(self.folded_regions.keys()):
            self.unfold_region(line)
        
        self.active = False
        self.delete("all")
        self.fold_icons.clear()

    def update_markers(self, event=None):
        """Обновляет позиции маркеров"""
        if not self.active:
            return
            
        try:
            # Очищаем старые маркеры
            self.delete("all")
            self.fold_icons.clear()
            
            # Рисуем новые маркеры
            for start_line, end_line in self.foldable_regions:
                try:
                    dline = self.text_widget.dlineinfo(f"{start_line}.0")
                    if dline:
                        y = dline[1] + dline[3] // 2
                        is_folded = start_line in self.folded_regions
                        
                        if is_folded:
                            # Треугольник вправо (свёрнуто)
                            icon_id = self.create_polygon(
                                5, y-4, 9, y, 5, y+4,
                                fill="#FFD600", outline="#FFD600", width=1
                            )
                        else:
                            # Треугольник вниз (развёрнуто)
                            icon_id = self.create_polygon(
                                7, y-4, 11, y, 7, y+4,
                                fill="#FFD600", outline="#FFD600", width=1
                            )
                        
                        self.fold_icons[start_line] = {
                            'icon_id': icon_id,
                            'end_line': end_line,
                            'folded': is_folded
                        }
                except:
                    pass
        except:
            pass

    def find_foldable_regions(self):
        """Находит области для сворачивания"""
        try:
            content = self.text_widget.get('1.0', 'end-1c')
            lines = content.split('\n')
            regions = []
            
            i = 0
            while i < len(lines):
                line = lines[i].strip()
                
                if (line.startswith('def ') or line.startswith('class ')) and line.endswith(':'):
                    start_line = i + 1
                    indent_level = len(lines[i]) - len(lines[i].lstrip())
                    end_line = start_line
                    
                    for j in range(i + 1, len(lines)):
                        if lines[j].strip() == '':
                            continue
                        current_indent = len(lines[j]) - len(lines[j].lstrip())
                        if current_indent <= indent_level and lines[j].strip() != '':
                            break
                        end_line = j + 1
                    
                    if end_line > start_line + 1:
                        regions.append((start_line, end_line))
                        
                i += 1
            
            return regions
        except:
            return []

    def on_click(self, event):
        """Обработка клика по маркеру"""
        for line, data in self.fold_icons.items():
            try:
                dline = self.text_widget.dlineinfo(f"{line}.0")
                if dline and abs(event.y - (dline[1] + dline[3] // 2)) < 10:
                    if data['folded']:
                        self.unfold_region(line)
                    else:
                        self.fold_region(line)
                    break
            except:
                pass

    def fold_region(self, start_line):
        """Сворачивает область"""
        try:
            if start_line not in self.fold_icons:
                return
                
            data = self.fold_icons[start_line]
            end_line = data['end_line']
            
            # Скрываем строки
            for line in range(start_line + 1, end_line + 1):
                self.text_widget.tag_add("folded", f"{line}.0", f"{line}.end+1c")
            
            self.text_widget.tag_configure("folded", elide=True)
            self.folded_regions[start_line] = end_line
            self.update_markers()
        except:
            pass

    def unfold_region(self, start_line):
        """Разворачивает область"""
        try:
            if start_line not in self.folded_regions:
                return
                
            end_line = self.folded_regions[start_line]
            self.text_widget.tag_remove("folded", f"{start_line+1}.0", f"{end_line+1}.0")
            del self.folded_regions[start_line]
            self.update_markers()
        except:
            pass

class ModernCodeFolding:
    def __init__(self, text_widget, line_numbers_canvas):
        self.text_widget = text_widget
        self.line_numbers = line_numbers_canvas
        self.folded_regions = {}
        self.fold_icons = {}
        self.foldable_regions = []
        self.active = False  # ДОБАВИТЬ!
        
        # Добавляем обработчик клика на Canvas номеров строк
        self.line_numbers.bind('<Button-1>', self.on_gutter_click)
        
        # ДОБАВИТЬ синхронизацию с прокруткой:
        self.text_widget.bind("<KeyRelease>", self.update_fold_icons)
        self.text_widget.bind("<MouseWheel>", self.update_fold_icons)
        self.text_widget.bind("<Button-4>", self.update_fold_icons)
        self.text_widget.bind("<Button-5>", self.update_fold_icons)
        self.text_widget.bind("<Configure>", self.update_fold_icons)
        
        # Перехватываем yview для синхронизации с прокруткой
        old_yview = self.text_widget.yview
        def new_yview(*args):
            result = old_yview(*args)
            if self.active:
                self.update_fold_icons()
            return result
        self.text_widget.yview = new_yview
        
    def add_fold_icons(self):
        """Добавляет современные треугольные иконки сворачивания"""
        self.active = True  # ДОБАВИТЬ!
        self.clear_fold_icons()
        self.foldable_regions = self.find_foldable_regions()
        self.update_fold_icons()
    
    def clear_fold_icons(self):  # ДОБАВИТЬ ЭТОТ МЕТОД!
        """Очищает все маркеры сворачивания"""
        try:
            for line, data in list(self.fold_icons.items()):
                try:
                    self.line_numbers.delete(data['icon_id'])
                except:
                    pass
            self.fold_icons.clear()
            
            # Разворачиваем все свернутые области
            for line in list(self.folded_regions.keys()):
                self.unfold_region(line)
            
            self.active = False
        except:
            pass
    
    def update_fold_icons(self, event=None):  # ДОБАВИТЬ ЭТОТ МЕТОД!
        """Обновляет позиции маркеров при прокрутке"""
        if not self.active or not self.foldable_regions:
            return
            
        try:
            # Очищаем старые маркеры
            for line, data in list(self.fold_icons.items()):
                try:
                    self.line_numbers.delete(data['icon_id'])
                except:
                    pass
            self.fold_icons.clear()
            
            # Перерисовываем маркеры на новых позициях
            for start_line, end_line in self.foldable_regions:
                try:
                    dline = self.text_widget.dlineinfo(f"{start_line}.0")
                    if dline:
                        y = dline[1] + dline[3] // 2
                        is_folded = start_line in self.folded_regions
                        
                        if is_folded:
                            icon_id = self.line_numbers.create_polygon(
                                13, y-4, 17, y, 13, y+4,
                                fill="#FFD600", outline="#FFD600", width=1
                            )
                        else:
                            icon_id = self.line_numbers.create_polygon(
                                15, y-4, 19, y, 15, y+4,
                                fill="#FFD600", outline="#FFD600", width=1
                            )
                        
                        self.fold_icons[start_line] = {
                            'icon_id': icon_id,
                            'end_line': end_line,
                            'folded': is_folded
                        }
                except:
                    pass
        except:
            pass
    
    def find_foldable_regions(self):
        """Находит области, которые можно свернуть"""
        content = self.text_widget.get('1.0', 'end-1c')
        lines = content.split('\n')
        regions = []
        
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            
            # Ищем определения функций и классов
            if (line.startswith('def ') or line.startswith('class ')) and line.endswith(':'):
                start_line = i + 1
                indent_level = len(lines[i]) - len(lines[i].lstrip())
                end_line = start_line
                
                # Ищем конец блока
                for j in range(i + 1, len(lines)):
                    if lines[j].strip() == '':
                        continue
                    current_indent = len(lines[j]) - len(lines[j].lstrip())
                    if current_indent <= indent_level and lines[j].strip() != '':
                        break
                    end_line = j + 1
                
                if end_line > start_line + 1:  # Только если есть что сворачивать
                    regions.append((start_line, end_line))
                    
            i += 1
        
        return regions

    def on_gutter_click(self, event):
        """Обработка клика по области номеров строк"""
        # Определяем строку по Y-координате
        for line, data in self.fold_icons.items():
            try:
                dline = self.text_widget.dlineinfo(f"{line}.0")
                if dline and abs(event.y - (dline[1] + dline[3] // 2)) < 10:
                    if data['folded']:
                        self.unfold_region(line)
                    else:
                        self.fold_region(line)
                    break
            except:
                pass
    
    def fold_region(self, start_line):
        """Современное сворачивание через elide"""
        if start_line not in self.fold_icons:
            return
            
        data = self.fold_icons[start_line]
        end_line = data['end_line']
        
        # Скрываем строки через elide (без изменения текста!)
        for line in range(start_line + 1, end_line + 1):
            self.text_widget.tag_add("folded", f"{line}.0", f"{line}.end+1c")
        
        self.text_widget.tag_configure("folded", elide=True)
        
        # Сохраняем состояние свёрнутости
        self.folded_regions[start_line] = end_line
        
        # Обновляем иконки
        self.update_fold_icons()
    
    def unfold_region(self, start_line):
        """Разворачивание области"""
        if start_line not in self.folded_regions:
            return
            
        end_line = self.folded_regions[start_line]
        
        # Показываем строки
        self.text_widget.tag_remove("folded", f"{start_line+1}.0", f"{end_line+1}.0")
        
        # Удаляем из свёрнутых
        del self.folded_regions[start_line]
        
        # Обновляем иконки
        self.update_fold_icons()




# Основной класс редактора
class CodeEditor(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("AI Code Editor")
        self.geometry("1200x800")
        self.iconbitmap("ikonka2.ico")
        self.font_size = 12

        # Настройка сетки
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # Статус-бар
        self.status_bar = ctk.CTkLabel(self, text="Готово", anchor='w')
        self.status_bar.grid(row=2, column=0, sticky='ew')

        # Панель поиска
        self.add_search_replace()
        self.search_frame.grid_remove()

        # Главный контейнер
        self.main_container = ctk.CTkFrame(self)
        self.main_container.grid(row=1, column=0, sticky='nsew')

        # Стиль вкладок
        style = ttk.Style()
        style.theme_use('default')
        style.configure('TNotebook', background='#2F3F4F', borderwidth=0)
        style.configure('TNotebook.Tab', background='#2F3F4F', foreground='#FFD600', padding=[12,8])
        style.map('TNotebook.Tab', 
                 background=[('selected', '#2F3F4F'), ('active', '#2F3F4F')],
                 foreground=[('selected', '#FFD600'), ('active', '#FFD600')])

        # Вкладки
        self.notebook = ttk.Notebook(self.main_container)
        self.notebook.pack(side='left', fill='both', expand=True)

        # AI-панель
        self.ai_panel = ctk.CTkFrame(self.main_container, width=300)
        self.ai_panel.pack(side='right', fill='y')
        self.init_ai_panel()

        self.tabs = {}
        self.tab_counter = 1
        self.create_new_tab()
        self.add_menu()

        self.setup_ai_integration()

        # --- Горячие клавиши
        self.setup_universal_hotkeys()
        self.bind('<Control-F5>', lambda event: self.run_code())
        self.bind('<Control-g>', lambda event: self.goto_line_dialog())
        self.bind('<Control-f>', lambda event: self.show_search_frame())
        self.bind('<Control-n>', lambda event: self.new_file())
        self.bind('<Control-o>', lambda event: self.open_file())
        self.bind('<Control-s>', lambda event: self.save_file())
        self.bind('<Control-z>', lambda event: self.get_current_text_area().edit_undo() if self.get_current_text_area() else None)
        self.bind('<Control-y>', lambda event: self.get_current_text_area().edit_redo() if self.get_current_text_area() else None)
        self.bind('<Control-c>', lambda event: self.copy_text())
        self.bind('<Control-v>', lambda event: self.paste_text())
        self.bind('<Control-plus>', self.zoom_in)
        self.bind('<Control-equal>', self.zoom_in)
        self.bind('<Control-minus>', self.zoom_out)
        self.bind('<Control-0>', self.reset_zoom)
        self.bind('<Control-Shift-A>', lambda event: self.analyze_code())
        self.bind('<Control-Shift-F>', lambda event: self.format_code())



    def apply_theme_to_existing_widgets(self):
        """Применяет тему к уже существующим виджетам"""
        try:
            # Получаем цвета из переменных
            bg_color = self.color_vars["bg_color"].get()
            accent_color = self.color_vars["accent_color"].get()
            text_color = self.color_vars["text_color"].get()
            button_color = self.color_vars["button_color"].get()
            border_color = self.color_vars["border_color"].get()
            hover_color = self.color_vars["hover_color"].get()
            
            # 1. Обновляем основные элементы интерфейса
            self.configure(fg_color=bg_color)
            self.main_container.configure(fg_color=bg_color)
            
            # 2. Обновляем статус-бар
            self.status_bar.configure(text_color=text_color)
            
            # 3. ПОЛНОЕ обновление AI-панели рекурсивно
            self.update_ai_panel_recursively(bg_color, text_color, accent_color, button_color, hover_color, border_color)
            
            # 4. ГЛАВНОЕ: Обновляем все текстовые области (tk.Text)
            self.update_text_areas_colors()
            
            # 5. Обновляем панель поиска (если она создана)
            if hasattr(self, 'search_frame'):
                self.update_search_panel_colors(bg_color, text_color, accent_color, button_color, hover_color, border_color)
            
            # 6. Обновляем окно кастомизации темы (если открыто)
            if hasattr(self, 'theme_window') and self.theme_window.winfo_exists():
                self.update_theme_window_colors(bg_color, text_color, accent_color, button_color, hover_color, border_color)
            
            self.update_status("Тема применена к интерфейсу")
            
        except Exception as e:
            print(f"Ошибка применения темы: {e}")

    def update_ai_panel_recursively(self, bg_color, text_color, accent_color, button_color, hover_color, border_color):
        """Рекурсивно обновляет все элементы AI-панели"""
        try:
            # Обновляем саму AI-панель
            self.ai_panel.configure(fg_color=bg_color)
            
            # Обновляем chat_history и user_input
            self.chat_history.configure(
                fg_color=bg_color, 
                text_color=text_color,
                border_color=border_color
            )
            self.user_input.configure(
                fg_color=bg_color, 
                text_color=text_color, 
                border_color=border_color
            )
            
            # Рекурсивно обновляем ВСЕ дочерние виджеты AI-панели
            self.update_widget_recursively(self.ai_panel, bg_color, text_color, accent_color, button_color, hover_color, border_color)
            
        except Exception as e:
            print(f"Ошибка обновления AI-панели: {e}")

    def update_widget_recursively(self, widget, bg_color, text_color, accent_color, button_color, hover_color, border_color):
        """Рекурсивно обновляет виджет и всех его потомков"""
        try:
            # Обновляем текущий виджет
            if isinstance(widget, ctk.CTkFrame):
                widget.configure(fg_color=bg_color, border_color=border_color)
            elif isinstance(widget, ctk.CTkButton):
                widget.configure(fg_color=button_color, hover_color=hover_color, border_color=border_color)
            elif isinstance(widget, ctk.CTkEntry):
                widget.configure(fg_color=bg_color, text_color=text_color, border_color=border_color)
            elif isinstance(widget, ctk.CTkLabel):
                widget.configure(text_color=text_color)
            elif isinstance(widget, ctk.CTkTextbox):
                widget.configure(fg_color=bg_color, text_color=text_color, border_color=border_color)
            
            # Рекурсивно обновляем всех потомков
            try:
                for child in widget.winfo_children():
                    self.update_widget_recursively(child, bg_color, text_color, accent_color, button_color, hover_color, border_color)
            except:
                pass
                
        except Exception as e:
            print(f"Ошибка обновления виджета {widget}: {e}")

    def update_search_panel_colors(self, bg_color, text_color, accent_color, button_color, hover_color, border_color):
        """Обновляет цвета панели поиска"""
        try:
            self.search_frame.configure(fg_color=bg_color)
            
            # Обновляем элементы поиска
            self.search_entry.configure(fg_color=bg_color, text_color=text_color, border_color=border_color)
            self.replace_entry.configure(fg_color=bg_color, text_color=text_color, border_color=border_color)
            
            # Обновляем все дочерние виджеты панели поиска
            self.update_widget_recursively(self.search_frame, bg_color, text_color, accent_color, button_color, hover_color, border_color)
            
        except Exception as e:
            print(f"Ошибка обновления панели поиска: {e}")

    def update_theme_window_colors(self, bg_color, text_color, accent_color, button_color, hover_color, border_color):
        """Обновляет цвета окна кастомизации темы"""
        try:
            self.theme_window.configure(fg_color=bg_color)
            
            # Рекурсивно обновляем все элементы окна темы
            self.update_widget_recursively(self.theme_window, bg_color, text_color, accent_color, button_color, hover_color, border_color)
            
        except Exception as e:
            print(f"Ошибка обновления окна темы: {e}")



    def update_all_tab_colors(self):
        """Обновляет цвета всех элементов вкладок"""
        try:
            bg_color = self.color_vars["bg_color"].get()
            border_color = self.color_vars["border_color"].get()
            
            # Обновляем стиль вкладок
            style = ttk.Style()
            style.configure('TNotebook', background=bg_color, borderwidth=0)
            style.configure('TNotebook.Tab', 
                           background=bg_color, 
                           foreground=self.color_vars["text_color"].get(), 
                           padding=[12,8])
            style.map('TNotebook.Tab', 
                     background=[('selected', bg_color), ('active', bg_color)],
                     foreground=[('selected', self.color_vars["text_color"].get()), 
                               ('active', self.color_vars["text_color"].get())])
            
            # Обновляем notebook
            self.notebook.configure(style='TNotebook')
            
        except Exception as e:
            print(f"Ошибка обновления вкладок: {e}")




    def update_text_areas_colors(self):
        """Обновляет цвета всех текстовых областей, номеров строк и разделителя"""
        try:
            bg_color = self.color_vars["bg_color"].get()
            text_color = self.color_vars["text_color"].get()
            accent_color = self.color_vars["accent_color"].get()
            border_color = self.color_vars["border_color"].get()
            
            for tab_frame, tab_data in self.tabs.items():
                # Основное текстовое поле
                text_area = tab_data['text_area']
                text_area.configure(
                    bg=bg_color,
                    fg=text_color,
                    insertbackground=accent_color,
                    selectbackground=accent_color,
                    selectforeground='#000000'
                )
                
                # Номера строк
                line_numbers = tab_data['line_numbers']
                line_numbers.configure(bg=bg_color)
                line_numbers.text_color = text_color  # <-- динамический цвет!
                line_numbers.redraw()
                
                # Разделитель
                if 'separator' in tab_data:
                    tab_data['separator'].configure(bg=border_color)
                
                # Маркеры сворачивания
                fold_markers = tab_data['fold_markers']
                fold_markers.configure(bg=bg_color)
                if fold_markers.active:
                    fold_markers.update_markers()
            
            self.update_syntax_highlighting_colors()
        except Exception as e:
            print(f"Ошибка обновления текстовых областей: {e}")


    def update_syntax_highlighting_colors(self):
        """Обновляет цвета подсветки синтаксиса для новой темы"""
        try:
            text_color = self.color_vars["text_color"].get()
            accent_color = self.color_vars["accent_color"].get()
            
            # Вычисляем цвета для подсветки на основе основных цветов темы
            if text_color == "#FFD600":  # Желтая тема
                keyword_color = '#FF79C6'
                string_color = '#A9DC76'
                comment_color = '#6272A4'
                function_color = '#78DCE8'
                number_color = '#BD93F9'
            elif text_color == "#3498DB":  # Синяя тема
                keyword_color = '#E74C3C'
                string_color = '#27AE60'
                comment_color = '#95A5A6'
                function_color = '#F39C12'
                number_color = '#9B59B6'
            elif text_color == "#27AE60":  # Зеленая тема
                keyword_color = '#E67E22'
                string_color = '#3498DB'
                comment_color = '#7F8C8D'
                function_color = '#8E44AD'
                number_color = '#E74C3C'
            else:
                # Для других цветов используем стандартные
                keyword_color = '#FF79C6'
                string_color = '#A9DC76'
                comment_color = '#6272A4'
                function_color = '#78DCE8'
                number_color = '#BD93F9'
            
            # Применяем новые цвета ко всем текстовым областям
            for tab_frame, tab_data in self.tabs.items():
                text_area = tab_data['text_area']
                
                # Обновляем теги подсветки
                text_area.tag_configure('keyword', foreground=keyword_color, font=('Consolas', self.font_size, 'bold'))
                text_area.tag_configure('string', foreground=string_color)
                text_area.tag_configure('comment', foreground=comment_color, font=('Consolas', self.font_size, 'italic'))
                text_area.tag_configure('function_def', foreground=function_color, font=('Consolas', self.font_size, 'bold'))
                text_area.tag_configure('function_call', foreground=function_color)
                text_area.tag_configure('number', foreground=number_color)
                text_area.tag_configure('operator', foreground=keyword_color)
                text_area.tag_configure('builtin', foreground=string_color)
                text_area.tag_configure('class_def', foreground=accent_color, font=('Consolas', self.font_size, 'bold'))
                text_area.tag_configure('decorator', foreground=string_color)
                text_area.tag_configure('self', foreground=keyword_color, font=('Consolas', self.font_size, 'italic'))
                text_area.tag_configure('import_module', foreground=accent_color)
                
                # Перерисовываем подсветку
                self.highlight_syntax_for_tab(text_area)
                
        except Exception as e:
            print(f"Ошибка обновления подсветки: {e}")


    def show_theme_customizer(self):
        """Показывает окно кастомизации темы"""
        self.theme_window = ctk.CTkToplevel(self)
        self.theme_window.title("Настройки темы")
        self.theme_window.geometry("600x700")
        self.theme_window.resizable(False, False)
        
        # Делаем окно модальным
        self.theme_window.transient(self)
        self.theme_window.grab_set()
        
        # Заголовок
        title_label = ctk.CTkLabel(self.theme_window, text="Кастомизация интерфейса", 
                                   font=ctk.CTkFont(size=18, weight="bold"))
        title_label.pack(pady=10)
        
        # Основной контейнер
        container = ctk.CTkFrame(self.theme_window)
        container.pack(fill="both", expand=True, padx=20, pady=10)
        
        # Словарь для хранения переменных цветов
        self.color_vars = {}
        
        # Настройки цветов
        color_settings = [
            ("Фон приложения", "bg_color", "#2F3F4F"),
            ("Акцентный цвет", "accent_color", "#FFD600"),
            ("Цвет текста", "text_color", "#FFD600"),
            ("Цвет при наведении", "hover_color", "#FFEA00"),
            ("Цвет рамок", "border_color", "#FFD600"),
            ("Цвет кнопок", "button_color", "#FFD600"),
            ("Цвет полос прокрутки", "scrollbar_color", "#555555")
        ]
        
        # Секция настройки цветов
        colors_label = ctk.CTkLabel(container, text="Настройка цветов:", 
                                   font=ctk.CTkFont(size=14, weight="bold"))
        colors_label.pack(pady=(10, 5))
        
        # Создаем элементы управления цветами
        for i, (label_text, var_name, default_color) in enumerate(color_settings):
            color_frame = ctk.CTkFrame(container)
            color_frame.pack(fill="x", pady=3, padx=10)
            
            # Метка
            label = ctk.CTkLabel(color_frame, text=label_text, width=150, anchor="w")
            label.pack(side="left", padx=10, pady=8)
            
            # Переменная для хранения цвета
            color_var = tk.StringVar(value=default_color)
            self.color_vars[var_name] = color_var
            
            # Поле ввода цвета
            color_entry = ctk.CTkEntry(color_frame, textvariable=color_var, width=100)
            color_entry.pack(side="left", padx=5, pady=8)
            
            # Кнопка выбора цвета
            color_button = ctk.CTkButton(color_frame, text="🎨", width=40, 
                                       command=lambda cv=color_var: self.choose_color(cv))
            color_button.pack(side="left", padx=5, pady=8)
            
            # Превью цвета
            preview_frame = ctk.CTkFrame(color_frame, width=30, height=25, fg_color=default_color)
            preview_frame.pack(side="right", padx=10, pady=8)
            
            # Привязываем обновление превью к изменению цвета
            color_var.trace_add("write", lambda *args, pf=preview_frame, cv=color_var: 
                               self.update_color_preview(pf, cv))
        
        # Предустановленные темы
        themes_label = ctk.CTkLabel(container, text="Готовые темы:", 
                                   font=ctk.CTkFont(size=14, weight="bold"))
        themes_label.pack(pady=(20, 5))
        
        # Кнопки тем в одном ряду
        theme_buttons_frame = ctk.CTkFrame(container)
        theme_buttons_frame.pack(fill="x", pady=5, padx=10)
        
        predefined_themes = [
            ("Темная", self.apply_dark_theme),
            ("Синяя", self.apply_blue_theme),
            ("Зеленая", self.apply_green_theme),
            ("Красная", self.apply_red_theme),
            ("Фиолетовая", self.apply_purple_theme)
        ]
        
        for theme_name, theme_func in predefined_themes:
            theme_btn = ctk.CTkButton(theme_buttons_frame, text=theme_name, 
                                    width=90, height=30, command=theme_func)
            theme_btn.pack(side="left", padx=3, pady=5)
        
        # Кнопки управления
        buttons_frame = ctk.CTkFrame(self.theme_window)
        buttons_frame.pack(side="bottom", fill="x", pady=10, padx=20)
        
        # Ряд 1: Предварительный просмотр и применение
        row1_frame = ctk.CTkFrame(buttons_frame)
        row1_frame.pack(fill="x", pady=2)
        
        preview_btn = ctk.CTkButton(row1_frame, text="Предварительный просмотр", 
                                   command=self.preview_theme)
        preview_btn.pack(side="left", padx=5, pady=5)
        
        apply_btn = ctk.CTkButton(row1_frame, text="Применить", 
                                 command=self.apply_custom_theme)
        apply_btn.pack(side="left", padx=5, pady=5)
        
        # Ряд 2: Сохранение и загрузка
        row2_frame = ctk.CTkFrame(buttons_frame)
        row2_frame.pack(fill="x", pady=2)
        
        save_btn = ctk.CTkButton(row2_frame, text="Сохранить тему", 
                                command=self.save_custom_theme)
        save_btn.pack(side="left", padx=5, pady=5)
        
        load_btn = ctk.CTkButton(row2_frame, text="Загрузить тему", 
                                command=self.load_custom_theme)
        load_btn.pack(side="left", padx=5, pady=5)
        
        close_btn = ctk.CTkButton(row2_frame, text="Закрыть", 
                                 command=self.theme_window.destroy)
        close_btn.pack(side="right", padx=5, pady=5)

    def update_color_preview(self, preview_frame, color_var):
        """Обновляет превью цвета"""
        try:
            color = color_var.get()
            if color and color.startswith('#') and len(color) == 7:
                preview_frame.configure(fg_color=color)
        except:
            pass


    def choose_color(self, color_var):
        """Открывает диалог выбора цвета"""
        try:
            from tkinter import colorchooser
            color = colorchooser.askcolor(title="Выберите цвет")[1]
            if color:
                color_var.set(color)
        except:
            messagebox.showerror("Ошибка", "Не удалось открыть палитру цветов")

    def update_color_preview(self, preview_frame, color_var):
        """Обновляет превью цвета"""
        try:
            color = color_var.get()
            if color and color.startswith('#') and len(color) == 7:
                preview_frame.configure(fg_color=color)
        except:
            pass

    def preview_theme(self):
        """Применяет тему для предварительного просмотра"""
        self.apply_theme_colors(preview=True)
        self.update_status("Предварительный просмотр темы")

    def apply_custom_theme(self):
        """Применяет выбранную тему"""
        self.apply_theme_colors(preview=False)
        self.update_status("Тема применена")

    def apply_theme_colors(self, preview=False):
        """Применяет цвета темы к интерфейсу"""
        try:
            # Получаем цвета из переменных
            bg_color = self.color_vars["bg_color"].get()
            accent_color = self.color_vars["accent_color"].get()
            text_color = self.color_vars["text_color"].get()
            hover_color = self.color_vars["hover_color"].get()
            border_color = self.color_vars["border_color"].get()
            button_color = self.color_vars["button_color"].get()
            
            # Создаем ПОЛНУЮ тему со всеми секциями
            temp_theme = {
                "CTk": {"fg_color": [bg_color, bg_color]},
                "CTkToplevel": {"fg_color": [bg_color, bg_color]},
                "CTkFrame": {
                    "corner_radius": 6,
                    "border_width": 0,
                    "fg_color": [bg_color, bg_color],
                    "top_fg_color": [bg_color, bg_color],
                    "border_color": [border_color, border_color]
                },
                "CTkButton": {
                    "corner_radius": 6,
                    "border_width": 0,
                    "fg_color": [button_color, button_color],
                    "hover_color": [hover_color, hover_color],
                    "border_color": [border_color, border_color],
                    "text_color": ["#000000", "#000000"],
                    "text_color_disabled": ["#666666", "#666666"]
                },
                "CTkLabel": {
                    "corner_radius": 0,
                    "fg_color": "transparent",
                    "text_color": [text_color, text_color]
                },
                "CTkEntry": {
                    "corner_radius": 6,
                    "border_width": 2,
                    "fg_color": [bg_color, bg_color],
                    "border_color": [border_color, border_color],
                    "text_color": [text_color, text_color],
                    "placeholder_text_color": ["#888888", "#888888"]
                },
                "CTkTextbox": {
                    "corner_radius": 6,
                    "border_width": 1,
                    "fg_color": [bg_color, bg_color],
                    "border_color": [border_color, border_color],
                    "text_color": [text_color, text_color],
                    "scrollbar_button_color": [accent_color, accent_color],
                    "scrollbar_button_hover_color": [hover_color, hover_color]
                },
                "CTkScrollbar": {
                    "corner_radius": 1000,
                    "border_spacing": 4,
                    "fg_color": "transparent",
                    "button_color": [accent_color, accent_color],
                    "button_hover_color": [hover_color, hover_color]
                },
                "CTkProgressBar": {
                    "corner_radius": 1000,
                    "border_width": 0,
                    "fg_color": ["#555555", "#555555"],
                    "progress_color": [accent_color, accent_color],
                    "border_color": ["#555555", "#555555"]
                },
                "CTkFont": {
                    "macOS": {
                        "family": "SF Display",
                        "size": 13,
                        "weight": "normal"
                    },
                    "Windows": {
                        "family": "Roboto",
                        "size": 13,
                        "weight": "normal"
                    },
                    "Linux": {
                        "family": "Roboto",
                        "size": 13,
                        "weight": "normal"
                    }
                }
            }
            
            # Сохраняем временную тему в файл
            temp_theme_path = "temp_theme.json"
            with open(temp_theme_path, 'w', encoding='utf-8') as f:
                json.dump(temp_theme, f, indent=2, ensure_ascii=False)
            
            # Применяем тему к новым CustomTkinter виджетам
            ctk.set_default_color_theme(temp_theme_path)
            
            # НОВОЕ: Применяем тему к СУЩЕСТВУЮЩИМ виджетам
            self.apply_theme_to_existing_widgets()
            
            if not preview:
                # Сохраняем как активную тему
                with open("tema.json", 'w', encoding='utf-8') as f:
                    json.dump(temp_theme, f, indent=2, ensure_ascii=False)
            
            # Убираем messagebox о перезапуске - теперь это не нужно!
            self.update_status("Тема применена!" if not preview else "Предварительный просмотр")
            
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось применить тему: {e}")

    def save_custom_theme(self):
        """Сохраняет пользовательскую тему"""
        try:
            from tkinter import filedialog
            file_path = filedialog.asksaveasfilename(
                defaultextension=".json",
                filetypes=[("JSON файлы", "*.json"), ("Все файлы", "*.*")],
                title="Сохранить тему как..."
            )
            
            if file_path:
                # Создаем тему на основе текущих настроек
                bg_color = self.color_vars["bg_color"].get()
                accent_color = self.color_vars["accent_color"].get()
                text_color = self.color_vars["text_color"].get()
                hover_color = self.color_vars["hover_color"].get()
                border_color = self.color_vars["border_color"].get()
                button_color = self.color_vars["button_color"].get()
                
                custom_theme = {
                    "CTk": {"fg_color": [bg_color, bg_color]},
                    "CTkToplevel": {"fg_color": [bg_color, bg_color]},
                    "CTkFrame": {
                        "corner_radius": 6,
                        "border_width": 0,
                        "fg_color": [bg_color, bg_color],
                        "top_fg_color": [bg_color, bg_color],
                        "border_color": [border_color, border_color]
                    },
                    "CTkButton": {
                        "corner_radius": 6,
                        "border_width": 0,
                        "fg_color": [button_color, button_color],
                        "hover_color": [hover_color, hover_color],
                        "border_color": [border_color, border_color],
                        "text_color": ["#000000", "#000000"]
                    },
                    "CTkLabel": {
                        "corner_radius": 0,
                        "fg_color": "transparent",
                        "text_color": [text_color, text_color]
                    },
                    "CTkEntry": {
                        "corner_radius": 6,
                        "border_width": 2,
                        "fg_color": [bg_color, bg_color],
                        "border_color": [border_color, border_color],
                        "text_color": [text_color, text_color]
                    }
                }
                
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(custom_theme, f, indent=2, ensure_ascii=False)
                
                messagebox.showinfo("Сохранение", "Тема успешно сохранена!")
                
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось сохранить тему: {e}")

    def load_custom_theme(self):
        """Загружает пользовательскую тему"""
        try:
            from tkinter import filedialog
            file_path = filedialog.askopenfilename(
                filetypes=[("JSON файлы", "*.json"), ("Все файлы", "*.*")],
                title="Загрузить тему"
            )
            
            if file_path:
                with open(file_path, 'r', encoding='utf-8') as f:
                    theme_data = json.load(f)
                
                # Извлекаем цвета из темы и устанавливаем в переменные
                try:
                    self.color_vars["bg_color"].set(theme_data["CTk"]["fg_color"][0])
                    self.color_vars["accent_color"].set(theme_data["CTkTextbox"]["scrollbar_button_color"][0])
                    self.color_vars["text_color"].set(theme_data["CTkLabel"]["text_color"][0])
                    self.color_vars["hover_color"].set(theme_data["CTkButton"]["hover_color"][0])
                    self.color_vars["border_color"].set(theme_data["CTkFrame"]["border_color"][0])
                    self.color_vars["button_color"].set(theme_data["CTkButton"]["fg_color"][0])
                    
                    messagebox.showinfo("Загрузка", "Тема успешно загружена!")
                except KeyError:
                    messagebox.showwarning("Внимание", "Файл темы имеет неправильный формат")
                    
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось загрузить тему: {e}")

    # Предустановленные темы
    def apply_dark_theme(self):
        """Применяет темную тему (по умолчанию)"""
        self.color_vars["bg_color"].set("#2F3F4F")
        self.color_vars["accent_color"].set("#FFD600")
        self.color_vars["text_color"].set("#FFD600")
        self.color_vars["hover_color"].set("#FFEA00")
        self.color_vars["border_color"].set("#FFD600")
        self.color_vars["button_color"].set("#FFD600")

    def apply_blue_theme(self):
        """Применяет синюю тему"""
        self.color_vars["bg_color"].set("#1E2A3A")
        self.color_vars["accent_color"].set("#3498DB")
        self.color_vars["text_color"].set("#3498DB")
        self.color_vars["hover_color"].set("#5DADE2")
        self.color_vars["border_color"].set("#3498DB")
        self.color_vars["button_color"].set("#3498DB")

    def apply_green_theme(self):
        """Применяет зеленую тему"""
        self.color_vars["bg_color"].set("#1A2E1A")
        self.color_vars["accent_color"].set("#27AE60")
        self.color_vars["text_color"].set("#27AE60")
        self.color_vars["hover_color"].set("#58D68D")
        self.color_vars["border_color"].set("#27AE60")
        self.color_vars["button_color"].set("#27AE60")

    def apply_red_theme(self):
        """Применяет красную тему"""
        self.color_vars["bg_color"].set("#2A1A1A")
        self.color_vars["accent_color"].set("#E74C3C")
        self.color_vars["text_color"].set("#E74C3C")
        self.color_vars["hover_color"].set("#F1948A")
        self.color_vars["border_color"].set("#E74C3C")
        self.color_vars["button_color"].set("#E74C3C")

    def apply_purple_theme(self):
        """Применяет фиолетовую тему"""
        self.color_vars["bg_color"].set("#2A1A2A")
        self.color_vars["accent_color"].set("#8E44AD")
        self.color_vars["text_color"].set("#8E44AD")
        self.color_vars["hover_color"].set("#BB8FCE")
        self.color_vars["border_color"].set("#8E44AD")
        self.color_vars["button_color"].set("#8E44AD")










    def setup_universal_hotkeys(self):
        """Настраивает универсальные горячие клавиши, работающие на любой раскладке"""
        
        # Привязываем универсальный обработчик
        self.bind_all('<Control-KeyPress>', self.handle_ctrl_hotkeys)
        self.bind_all('<KeyPress-F5>', lambda event: self.run_code())
        self.bind_all('<Control-F5>', lambda event: self.run_code())
        
        # Специальные клавиши (работают на любой раскладке)
        self.bind_all('<Control-plus>', self.zoom_in)
        self.bind_all('<Control-equal>', self.zoom_in)  # Ctrl+=
        self.bind_all('<Control-minus>', self.zoom_out)
        self.bind_all('<Control-0>', self.reset_zoom)

    def handle_ctrl_hotkeys(self, event):
        """Универсальный обработчик Ctrl+клавиши (работает на любой раскладке)"""
        
        # Проверяем, что действительно нажат Ctrl
        if not (event.state & 0x4):
            return
        
        keycode = event.keycode
        
        # Базовые операции с файлами
        if keycode == 78:  # N/Т - Новый файл
            self.new_file()
            return "break"
        elif keycode == 79:  # O/Щ - Открыть файл
            self.open_file()
            return "break"
        elif keycode == 83:  # S/Ы - Сохранить файл
            self.save_file()
            return "break"
        
        # Операции редактирования
        elif keycode == 90:  # Z/Я - Отменить
            text_area = self.get_current_text_area()
            if text_area:
                text_area.edit_undo()
            return "break"
        elif keycode == 89:  # Y/Н - Повторить
            text_area = self.get_current_text_area()
            if text_area:
                text_area.edit_redo()
            return "break"
        elif keycode == 67:  # C/С - Копировать
            self.copy_text()
            return "break"
        elif keycode == 86:  # V/М - Вставить
            self.paste_text()
            return "break"
        elif keycode == 88:  # X/Ч - Вырезать
            self.cut_text()
            return "break"
        
        # Поиск и навигация
        elif keycode == 70:  # F/А - Найти и заменить
            self.show_search_frame()
            return "break"
        elif keycode == 71:  # G/П - Перейти к строке
            self.goto_line_dialog()
            return "break"
        
        # AI функции (с проверкой Shift)
        elif keycode == 65 and (event.state & 0x1):  # Ctrl+Shift+A/Ф - Анализ кода
            self.analyze_code()
            return "break"
        elif keycode == 70 and (event.state & 0x1):  # Ctrl+Shift+F/А - Форматировать код
            self.format_code()
            return "break"
        
        # НОВЫЕ ГОРЯЧИЕ КЛАВИШИ ДЛЯ СВОРАЧИВАНИЯ КОДА
        elif keycode == 81:  # Q/Й - Показать/скрыть маркеры сворачивания
            self.toggle_code_folding()
            return "break"
        elif keycode == 87:  # W/Ц - Свернуть текущий блок
            self.fold_current_block()
            return "break"
        elif keycode == 69:  # E/У - Развернуть текущий блок
            self.unfold_current_block()
            return "break"
        elif keycode == 82:  # R/К - Развернуть все блоки
            self.unfold_all_blocks()
            return "break"

    def cut_text(self):
        """Вырезать выделенный текст"""
        text_area = self.get_current_text_area()
        if text_area:
            try:
                selected = text_area.get("sel.first", "sel.last")
                self.clipboard_clear()
                self.clipboard_append(selected)
                text_area.delete("sel.first", "sel.last")
                self.update_status("Текст вырезан")
            except tk.TclError:
                pass

    def fold_current_block(self):
        """Сворачивает блок кода на текущей строке"""
        current_tab_id = self.notebook.select()
        if not current_tab_id:
            return
        current_tab = self.nametowidget(current_tab_id)
        if current_tab in self.tabs and 'fold_markers' in self.tabs[current_tab]:
            markers = self.tabs[current_tab]['fold_markers']
            text_area = self.tabs[current_tab]['text_area']
            
            # Получаем текущую строку
            current_pos = text_area.index(tk.INSERT)
            current_line = int(current_pos.split('.')[0])
            
            # Ищем блок для сворачивания на текущей строке или выше
            for start_line, end_line in markers.foldable_regions:
                if start_line <= current_line <= end_line:
                    markers.fold_region(start_line)
                    self.update_status(f"Блок свёрнут (строки {start_line}-{end_line})")
                    return
            
            self.update_status("Блок для сворачивания не найден")

    def unfold_current_block(self):
        """Разворачивает блок кода на текущей строке"""
        current_tab_id = self.notebook.select()
        if not current_tab_id:
            return
        current_tab = self.nametowidget(current_tab_id)
        if current_tab in self.tabs and 'fold_markers' in self.tabs[current_tab]:
            markers = self.tabs[current_tab]['fold_markers']
            text_area = self.tabs[current_tab]['text_area']
            
            # Получаем текущую строку
            current_pos = text_area.index(tk.INSERT)
            current_line = int(current_pos.split('.')[0])
            
            # Ищем свёрнутый блок для разворачивания
            for start_line in list(markers.folded_regions.keys()):
                end_line = markers.folded_regions[start_line]
                if start_line <= current_line:
                    markers.unfold_region(start_line)
                    self.update_status(f"Блок развёрнут (строки {start_line}-{end_line})")
                    return
            
            self.update_status("Свёрнутый блок не найден")

    def unfold_all_blocks(self):
        """Разворачивает все свёрнутые блоки"""
        current_tab_id = self.notebook.select()
        if not current_tab_id:
            return
        current_tab = self.nametowidget(current_tab_id)
        if current_tab in self.tabs and 'fold_markers' in self.tabs[current_tab]:
            markers = self.tabs[current_tab]['fold_markers']
            count = len(markers.folded_regions)
            
            # Разворачиваем все блоки
            for start_line in list(markers.folded_regions.keys()):
                markers.unfold_region(start_line)
            
            if count > 0:
                self.update_status(f"Развёрнуто {count} блоков")
            else:
                self.update_status("Нет свёрнутых блоков")




    # ------------------------- СТАТУС-БАР --------------------------------------
    def update_status(self, message):
        """Обновляет статус-бар"""
        self.status_bar.configure(text=message)
        self.after(3000, lambda: self.status_bar.configure(text="Готово"))


    def cancel_code_folding(self):
        """Полностью убирает все маркеры и разворачивает код"""
        current_tab_id = self.notebook.select()
        if not current_tab_id:
            return
        current_tab = self.nametowidget(current_tab_id)
        if current_tab in self.tabs and 'fold_markers' in self.tabs[current_tab]:
            markers = self.tabs[current_tab]['fold_markers']
            markers.hide_markers()
            self.update_status("Сворачивание кода отменено")

    def show_about(self):
        """Показывает информацию о программе"""
        about_win = ctk.CTkToplevel(self)
        about_win.title("О программе")
        about_win.geometry("400x300")
        about_win.resizable(False, False)
        
        info_frame = ctk.CTkFrame(about_win)
        info_frame.pack(fill='both', expand=True, padx=20, pady=20)
        
        title_label = ctk.CTkLabel(info_frame, text="AI Code Editor", 
                                  font=('Arial', 20, 'bold'))
        title_label.pack(pady=10)
        
        version_label = ctk.CTkLabel(info_frame, text="Версия 1.2", 
                                    font=('Arial', 12))
        version_label.pack(pady=5)
        
        description = ctk.CTkTextbox(info_frame, height=150)
        description.pack(fill='both', expand=True, padx=10, pady=10)
        
        about_text = """AI Code Editor - современный редактор кода с поддержкой:

    • Подсветка синтаксиса Python
    • Автодополнение кода (Jedi)
    • Сворачивание блоков кода
    • Многострочный курсор
    • Интеграция с AI (Perplexity)
    • Анализ и форматирование кода
    • Поиск и замена
    • Запуск кода с графическим вводом

    Разработано для учебных целей.
    © 2024 AI Code Editor Team"""
        
        description.insert('1.0', about_text)
        description.configure(state="disabled")
        
        ctk.CTkButton(about_win, text="Закрыть", command=about_win.destroy).pack(pady=10)


    def setup_autocomplete(self, text_area):
        """Настраивает автодополнение для текстовой области"""
        if not JEDI_AVAILABLE:
            return
            
        def show_autocomplete(event):
            if event.keysym == 'period' or (event.char and event.char.isalpha()):
                # Небольшая задержка для обновления текста
                self.after(10, lambda: self.try_autocomplete(text_area))
        
        text_area.bind('<KeyRelease>', show_autocomplete)

    def try_autocomplete(self, text_area):
        """Пытается показать автодополнение"""
        if not JEDI_AVAILABLE:
            return
            
        try:
            # Получаем текущую позицию
            current_pos = text_area.index(tk.INSERT)
            row, col = map(int, current_pos.split('.'))
            
            # Получаем весь код
            code = text_area.get('1.0', 'end-1c')
            
            # Создаем скрипт Jedi
            script = jedi.Script(code=code, line=row, column=col, path='')
            completions = script.completions()
            
            if completions and len(completions) > 1:
                # Вычисляем позицию окна автодополнения
                bbox = text_area.bbox(current_pos)
                if bbox:
                    x = text_area.winfo_rootx() + bbox[0]
                    y = text_area.winfo_rooty() + bbox[1] + bbox[3]
                    
                    # Показываем окно автодополнения
                    AutocompletePopup(self, text_area, completions, x, y)
                    
        except Exception as e:
            pass  # Игнорируем ошибки автодополнения

    # ------------------------- AI-ПАНЕЛЬ ---------------------------------------
    def init_ai_panel(self):
        ai_title = ctk.CTkLabel(self.ai_panel, text="AI Помощник", font=('Arial', 12, 'bold'))
        ai_title.pack(pady=5)
        
        self.chat_history = ctk.CTkTextbox(self.ai_panel, height=400, width=300)
        self.chat_history.pack(fill='both', expand=True, padx=5, pady=5)
        self.chat_history.configure(state="disabled")
        
        # СНАЧАЛА создаем input_frame
        input_frame = ctk.CTkFrame(self.ai_panel)
        input_frame.pack(fill='x', padx=5, pady=5)
        
        # ПОТОМ создаем user_input внутри input_frame
        self.user_input = ctk.CTkEntry(input_frame)
        self.user_input.pack(side='left', fill='x', expand=True, padx=(0, 5))
        self.user_input.bind('<Return>', self.send_query)
        
        # Добавляем бинды для копирования/вставки
        self.user_input.bind("<Control-v>", entry_paste)
        self.user_input.bind("<Control-V>", entry_paste)
        self.user_input.bind("<Control-c>", entry_copy)
        self.user_input.bind("<Control-C>", entry_copy)
        self.user_input.bind("<Control-x>", entry_cut)
        self.user_input.bind("<Control-X>", entry_cut)
        
        # Кнопка отправки
        send_btn = ctk.CTkButton(input_frame, text="Отправить", command=self.send_query)
        send_btn.pack(side='right')


    # ------------------------- СОЗДАНИЕ ВКЛАДКИ (ИСПРАВЛЕНО) --------------------------------
    def create_new_tab(self, content="", filename=None):
        tab_frame = ttk.Frame(self.notebook)

        # Создаем основное текстовое поле
        text_area = tk.Text(
            tab_frame, wrap='none',
            bg='#2F3F4F',
            fg='#FFD600',
            insertbackground='#FFD600',
            font=('Consolas', self.font_size),
            undo=True,
            relief='flat',
            borderwidth=0,
            highlightthickness=0
        )

        # МАРКЕРЫ СВОРАЧИВАНИЯ (СЛЕВА)
        fold_markers = FoldingMarkers(tab_frame, text_area)
        fold_markers.pack(side='left', fill='y')

        # НОМЕРА СТРОК (ПОСЕРЕДИНЕ)
        line_numbers = TextLineNumbers(tab_frame, text_area, width=50)
        line_numbers.pack(side='left', fill='y')

        # ИСПРАВЛЕНИЕ: Разделительная линия без жестко заданного цвета
        separator = tk.Frame(tab_frame, width=2, bg='#FFD600')  # Временно оставляем
        separator.pack(side='left', fill='y')

        # Упаковываем текстовое поле
        text_area.pack(side='left', fill='both', expand=True)

        # Скроллбар
        scrollbar = ttk.Scrollbar(tab_frame, command=text_area.yview)
        scrollbar.pack(side='right', fill='y')
        text_area.config(yscrollcommand=scrollbar.set)

        # Привязываем события
        text_area.bind('<Control-z>', lambda event: text_area.edit_undo())
        text_area.bind('<Control-y>', lambda event: text_area.edit_redo())

        if content:
            text_area.insert('1.0', content)

        if filename:
            tab_name = os.path.basename(filename)
        else:
            tab_name = f"Новый файл {self.tab_counter}"
            self.tab_counter += 1

        self.notebook.add(tab_frame, text=tab_name)
        self.notebook.select(tab_frame)
        
        # СНАЧАЛА создаем словарь
        self.tabs[tab_frame] = {
            'text_area': text_area,
            'filename': filename,
            'line_numbers': line_numbers,
            'fold_markers': fold_markers,
            'separator': separator  # ДОБАВЛЯЕМ РАЗДЕЛИТЕЛЬ В СЛОВАРЬ
        }
        
        self.setup_syntax_highlighting_for_tab(text_area)
        self.setup_autocomplete(text_area)
        line_numbers.redraw()
        return text_area








    def solve_linear_equation(self):
            win = ctk.CTkToplevel(self)
            win.title("Решение уравнения ax + b = c")
            win.geometry("350x220")
            
            entries = {}
            for i, (label, sym) in enumerate([('a', 'a'), ('b', 'b'), ('c', 'c')]):
                ctk.CTkLabel(win, text=f"{label}:").grid(row=i, column=0, padx=5, pady=5)
                entries[sym] = ctk.CTkEntry(win)
                entries[sym].grid(row=i, column=1, padx=5, pady=5)
            
            result_label = ctk.CTkLabel(win, text="")
            result_label.grid(row=3, columnspan=2)

            def calculate():
                try:
                    a = float(entries['a'].get())
                    b = float(entries['b'].get())
                    c = float(entries['c'].get())
                except ValueError:
                    result_label.configure(text="Ошибка: введите числа!", text_color="red")
                    return

                if a == 0:
                    result = "Бесконечно решений" if b == c else "Нет решений"
                else:
                    x = (c - b) / a
                    result = f"x = {x:.2f}"
                result_label.configure(text=result, text_color="#FFD600")

            btn_frame = ctk.CTkFrame(win)
            btn_frame.grid(row=4, columnspan=2, pady=10)
            ctk.CTkButton(btn_frame, text="Решить", command=calculate).pack(side='left', padx=5)
            ctk.CTkButton(btn_frame, text="Закрыть", command=win.destroy).pack(side='right', padx=5)


    # ------------------------- ПОИСК И ЗАМЕНА ----------------------------------
    def add_search_replace(self):
        self.search_frame = ctk.CTkFrame(self)  # СНАЧАЛА создаём фрейм!
        ctk.CTkLabel(self.search_frame, text="Найти:").grid(row=0, column=0, padx=5, pady=5)
        self.search_entry = ctk.CTkEntry(self.search_frame, width=200)
        self.search_entry.grid(row=0, column=1, padx=5, pady=5)
        ctk.CTkLabel(self.search_frame, text="Заменить:").grid(row=0, column=2, padx=5, pady=5)
        self.replace_entry = ctk.CTkEntry(self.search_frame, width=200)
        self.replace_entry.grid(row=0, column=3, padx=5, pady=5)
        ctk.CTkButton(self.search_frame, text="Найти", command=lambda: self.find_text(self.search_entry.get())).grid(row=0, column=4, padx=5, pady=5)
        ctk.CTkButton(self.search_frame, text="Заменить", command=lambda: self.replace_text(self.search_entry.get(), self.replace_entry.get())).grid(row=0, column=5, padx=5, pady=5)
        ctk.CTkButton(self.search_frame, text="Закрыть", command=self.hide_search_frame).grid(row=0, column=6, padx=5, pady=5)
        self.search_frame.grid_columnconfigure(1, weight=1)
        self.search_frame.grid_columnconfigure(3, weight=1)

        # Бинды для вставки/копирования
        self.search_entry.bind("<Control-v>", entry_paste)
        self.search_entry.bind("<Control-V>", entry_paste)
        self.search_entry.bind("<Control-c>", entry_copy)
        self.search_entry.bind("<Control-C>", entry_copy)
        self.search_entry.bind("<Control-x>", entry_cut)
        self.search_entry.bind("<Control-X>", entry_cut)
        self.replace_entry.bind("<Control-v>", entry_paste)
        self.replace_entry.bind("<Control-V>", entry_paste)
        self.replace_entry.bind("<Control-c>", entry_copy)
        self.replace_entry.bind("<Control-C>", entry_copy)
        self.replace_entry.bind("<Control-x>", entry_cut)
        self.replace_entry.bind("<Control-X>", entry_cut)


    def show_search_frame(self):
        self.search_frame.grid()
        self.search_entry.focus_set()

    def hide_search_frame(self):
        self.search_frame.grid_remove()

    def goto_line_dialog(self):
        import tkinter.simpledialog
        text_area = self.get_current_text_area()
        if not text_area:
            return
        total_lines = int(text_area.index('end-1c').split('.')[0])
        line = tkinter.simpledialog.askinteger(
            "Перейти к строке",
            f"Введите номер строки (1 - {total_lines}):",
            minvalue=1,
            maxvalue=total_lines
        )
        if line:
            text_area.mark_set("insert", f"{line}.0")
            text_area.see(f"{line}.0")
            text_area.focus_set()

    def find_text(self, query):
        if not query:
            return
        current_tab_id = self.notebook.select()
        if not current_tab_id:
            return
        current_tab = self.nametowidget(current_tab_id)
        if current_tab not in self.tabs:
            messagebox.showerror("Ошибка", "Вкладка не найдена")
            return
        text_area = self.tabs[current_tab]['text_area']
        text_area.tag_remove('search', '1.0', tk.END)
        start_pos = '1.0'
        count = 0
        while True:
            start_pos = text_area.search(query, start_pos, stopindex=tk.END)
            if not start_pos:
                break
            end_pos = f"{start_pos}+{len(query)}c"
            text_area.tag_add('search', start_pos, end_pos)
            start_pos = end_pos
            count += 1
        text_area.tag_config('search', background='#FFD600', foreground='#000000')
        if count == 0:
            messagebox.showinfo("Поиск", f"Текст '{query}' не найден")
        else:
            messagebox.showinfo("Поиск", f"Найдено {count} совпадений")

    def replace_text(self, find_query, replace_with):
        if not find_query:
            return
        current_tab_id = self.notebook.select()
        if not current_tab_id:
            return
        current_tab = self.nametowidget(current_tab_id)
        if current_tab not in self.tabs:
            messagebox.showerror("Ошибка", "Вкладка не найдена")
            return
        text_area = self.tabs[current_tab]['text_area']
        content = text_area.get('1.0', tk.END)
        if find_query in content:
            new_content = content.replace(find_query, replace_with)
            text_area.delete('1.0', tk.END)
            text_area.insert('1.0', new_content)
            count = content.count(find_query)
            messagebox.showinfo("Замена", f"Заменено {count} совпадений")
        else:
            messagebox.showinfo("Замена", f"Текст '{find_query}' не найден")

    # ------------------------- АНАЛИЗ КОДА ЧЕРЕЗ AI (ОПТИМИЗИРОВАНО) -----------
    def analyze_code(self):
        """Анализирует код через AI в отдельном потоке"""
        text_area = self.get_current_text_area()
        if not text_area:
            messagebox.showwarning("Внимание", "Нет открытого файла для анализа.")
            return
        code = text_area.get('1.0', tk.END).strip()
        if not code:
            messagebox.showinfo("Анализ кода", "Файл пуст — нечего анализировать.")
            return

        self.update_status("Анализируется код...")
        
        progress_win = ctk.CTkToplevel(self)
        progress_win.title("Анализ кода")
        progress_win.geometry("300x100")
        progress_win.resizable(False, False)
        
        progress_label = ctk.CTkLabel(progress_win, text="Анализируется код...\nПожалуйста, подождите.")
        progress_label.pack(pady=20)
        
        progress_bar = ctk.CTkProgressBar(progress_win, mode='indeterminate')
        progress_bar.pack(pady=10, padx=20, fill='x')
        progress_bar.start()

        def worker():
            prompt = (
                "Ты опытный преподаватель программирования. Проанализируй этот Python-код и дай советы новичку:\n\n"
                f"{code}\n\n"
                "Проверь:\n"
                "1. Синтаксические ошибки\n"
                "2. Логические ошибки\n"
                "3. Стиль кода (PEP 8)\n"
                "4. Возможные улучшения\n"
                "5. Советы по оптимизации\n"
                "Отвечай на русском языке, дружелюбно и понятно для начинающего программиста."
            )
            try:
                response = client.query(prompt)
                self.update_status("Анализ завершён")
            except Exception as e:
                response = f"Ошибка анализа: {e}"
                self.update_status("Ошибка анализа")
            
            progress_win.destroy()
            self.show_analysis_result(response)

        threading.Thread(target=worker, daemon=True).start()

    def format_code(self):
        """Форматирует код через AI в отдельном потоке"""
        text_area = self.get_current_text_area()
        if not text_area:
            messagebox.showwarning("Внимание", "Нет открытого файла для форматирования.")
            return
        code = text_area.get('1.0', tk.END).strip()
        if not code:
            messagebox.showinfo("Форматирование", "Файл пуст.")
            return

        self.update_status("Форматируется код...")
        
        progress_win = ctk.CTkToplevel(self)
        progress_win.title("Форматирование кода")
        progress_win.geometry("300x100")
        progress_win.resizable(False, False)
        
        progress_label = ctk.CTkLabel(progress_win, text="Форматируется код...\nПожалуйста, подождите.")
        progress_label.pack(pady=20)
        
        progress_bar = ctk.CTkProgressBar(progress_win, mode='indeterminate')
        progress_bar.pack(pady=10, padx=20, fill='x')
        progress_bar.start()

        def worker():
            prompt = f"Отформатируй этот Python-код согласно стандарту PEP8. Верни только код без дополнительных объяснений:\n\n{code}"
            try:
                response = client.query(prompt)
                formatted_code = response.strip()
                if formatted_code.startswith("```"):
                    formatted_code = formatted_code[3:]
                if formatted_code.endswith("```"):
                    formatted_code = formatted_code[:-3]
                formatted_code = formatted_code.strip()
                
                text_area.delete('1.0', tk.END)
                text_area.insert('1.0', formatted_code)
                self.update_status("Код отформатирован")
            except Exception as e:
                self.update_status("Ошибка форматирования")
                messagebox.showerror("Ошибка форматирования", f"Не удалось отформатировать код: {str(e)}")
            
            progress_win.destroy()

        threading.Thread(target=worker, daemon=True).start()

    def show_analysis_result(self, analysis):
        """Показывает результат анализа кода в отдельном окне"""
        result_win = ctk.CTkToplevel(self)
        result_win.title("Результат анализа кода")
        result_win.geometry("700x500")

        result_text = ctk.CTkTextbox(result_win, font=('Consolas', 11), wrap='word')
        result_text.pack(fill='both', expand=True, padx=10, pady=10)

        result_text.insert('1.0', analysis)
        
        button_frame = ctk.CTkFrame(result_win)
        button_frame.pack(pady=5)
        
        ctk.CTkButton(button_frame, text="Сохранить в файл", command=lambda: self.save_analysis(analysis)).pack(side='left', padx=5)
        ctk.CTkButton(button_frame, text="Закрыть", command=result_win.destroy).pack(side='right', padx=5)

    def save_analysis(self, analysis):
        """Сохраняет анализ в текстовый файл"""
        file_path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Текстовые файлы", "*.txt"), ("Все файлы", "*.*")]
        )
        if file_path:
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(analysis)
                messagebox.showinfo("Сохранение", "Анализ сохранён в файл")
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось сохранить файл: {e}")

    def insert_template(self):
        """Вставляет шаблон основного блока Python"""
        text_area = self.get_current_text_area()
        if text_area:
            template = '''def main():
    """Основная функция программы"""
    pass

if __name__ == "__main__":
    main()'''
            text_area.insert("insert", template)

    def show_help(self):
        """Показывает справку по Python"""
        help_win = ctk.CTkToplevel(self)
        help_win.title("Справка по Python")
        help_win.geometry("600x400")
        
        help_text = ctk.CTkTextbox(help_win, font=('Consolas', 11))
        help_text.pack(fill='both', expand=True, padx=10, pady=10)
        
        help_content = """
СПРАВКА ПО PYTHON

Основные конструкции:

1. ПЕРЕМЕННЫЕ:
   name = "Иван"
   age = 25
   pi = 3.14

2. УСЛОВИЯ:
   if age >= 18:
       print("Совершеннолетний")
   else:
       print("Несовершеннолетний")

3. ЦИКЛЫ:
   for i in range(5):
       print(i)
   
   while x > 0:
       x -= 1

4. ФУНКЦИИ:
   def greet(name):
       return f"Привет, {name}!"

5. СПИСКИ:
   numbers = [1, 2, 3, 4, 5]
   numbers.append(6)

6. СЛОВАРИ:
   person = {"name": "Иван", "age": 25}
   print(person["name"])

Горячие клавиши редактора:
- F5: Запустить код
- Ctrl+S: Сохранить
- Ctrl+F: Найти и заменить
- Ctrl+Shift+A: Анализ кода
- Ctrl+Shift+F: Форматировать код
"""
        
        help_text.insert('1.0', help_content)
        
        ctk.CTkButton(help_win, text="Закрыть", command=help_win.destroy).pack(pady=5)

    # ------------------------- МЕНЮ --------------------------------------------
    def add_menu(self):
        menu_bar = tk.Menu(self)
        
        file_menu = tk.Menu(menu_bar, tearoff=0)
        file_menu.add_command(label="Новый", command=self.new_file, accelerator="Ctrl+N")
        file_menu.add_command(label="Открыть", command=self.open_file, accelerator="Ctrl+O")
        file_menu.add_command(label="Сохранить", command=self.save_file, accelerator="Ctrl+S")
        file_menu.add_separator()
        file_menu.add_command(label="Вставить шаблон", command=self.insert_template)
        file_menu.add_separator()
        file_menu.add_command(label="Запустить (F5)", command=self.run_code, accelerator="F5")
        menu_bar.add_cascade(label="Файл", menu=file_menu)
        
        edit_menu = tk.Menu(menu_bar, tearoff=0)
        edit_menu.add_separator()
        edit_menu.add_command(label="Показать маркеры сворачивания", command=self.toggle_code_folding)
        edit_menu.add_command(label="Отмена сворачивания", command=self.cancel_code_folding)
        edit_menu.add_command(label="Отменить", command=lambda: self.get_current_text_area().edit_undo(), accelerator="Ctrl+Z")
        edit_menu.add_command(label="Повторить", command=lambda: self.get_current_text_area().edit_redo(), accelerator="Ctrl+Y")
        edit_menu.add_separator()
        edit_menu.add_command(label="Копировать", command=lambda: self.copy_text(), accelerator="Ctrl+C")
        edit_menu.add_command(label="Вставить", command=lambda: self.paste_text(), accelerator="Ctrl+V")
        edit_menu.add_separator()
        edit_menu.add_command(label="Найти и заменить", command=self.show_search_frame, accelerator="Ctrl+F")
        edit_menu.add_command(label="Перейти к строке...", command=self.goto_line_dialog, accelerator="Ctrl+G")
        edit_menu.add_separator()
        edit_menu.add_command(label="Увеличить шрифт", command=self.zoom_in, accelerator="Ctrl++")
        edit_menu.add_command(label="Уменьшить шрифт", command=self.zoom_out, accelerator="Ctrl+-")
        edit_menu.add_command(label="Сбросить масштаб", command=self.reset_zoom, accelerator="Ctrl+0")
        menu_bar.add_cascade(label="Правка", menu=edit_menu)
        
        ai_menu = tk.Menu(menu_bar, tearoff=0)
        ai_menu.add_command(label="Анализ кода", command=self.analyze_code, accelerator="Ctrl+Shift+A")
        ai_menu.add_command(label="Форматировать код", command=self.format_code, accelerator="Ctrl+Shift+F")
        menu_bar.add_cascade(label="AI", menu=ai_menu)
        
        help_menu = tk.Menu(menu_bar, tearoff=0)
        help_menu.add_command(label="Справка по Python", command=self.show_help)
        help_menu.add_command(label="О программе", command=self.show_about)
        menu_bar.add_cascade(label="Справка", menu=help_menu)
        # В edit_menu добавьте:
        edit_menu.add_separator()
        edit_menu.add_command(label="Настройки темы...", command=self.show_theme_customizer)

        
        self.config(menu=menu_bar)

    def toggle_code_folding(self):
        """Включает/выключает маркеры сворачивания кода"""
        current_tab_id = self.notebook.select()
        if not current_tab_id:
            return
        current_tab = self.nametowidget(current_tab_id)
        if current_tab in self.tabs and 'fold_markers' in self.tabs[current_tab]:
            markers = self.tabs[current_tab]['fold_markers']
            if markers.active:
                markers.hide_markers()
                self.update_status("Маркеры сворачивания скрыты")
            else:
                markers.show_markers()
                self.update_status("Маркеры сворачивания показаны")



    # ------------------------- ЗУМ ---------------------------------------------
    def zoom_in(self, event=None):
        self.font_size += 1
        self.update_all_fonts()

    def zoom_out(self, event=None):
        if self.font_size > 6:
            self.font_size -= 1
            self.update_all_fonts()

    def reset_zoom(self, event=None):
        self.font_size = 12
        self.update_all_fonts()

    def update_all_fonts(self):
        for tab in self.tabs.values():
            tab['text_area'].config(font=('Consolas', self.font_size))
            # Исправлено: проверяем правильное имя ключа
            if 'line_numbers' in tab and hasattr(tab['line_numbers'], 'redraw'):
                tab['line_numbers'].redraw()



    # ------------------------- ЗАПУСК КОДА -------------------------------------
    def custom_input(self, prompt="Введите значение: "):
        """Создает диалог для ввода данных"""
        dialog = UserInputDialog(self, prompt)
        self.wait_window(dialog)
        return dialog.result if dialog.result is not None else ""

    def run_code(self):
        text_area = self.get_current_text_area()
        if not text_area:
            messagebox.showwarning("Внимание", "Нет открытого файла для запуска.")
            return
        original_code = text_area.get('1.0', tk.END)
        self.update_status("Выполняется код...")

        # Проверяем, есть ли input() в коде
        if "input(" in original_code:
            modified_code = self.handle_input_in_code(original_code)
            if modified_code is None:
                self.update_status("Отменено пользователем")
                return
        else:
            modified_code = original_code

        try:
            with tempfile.NamedTemporaryFile('w', delete=False, suffix='.py', encoding='utf-8') as tmp:
                tmp.write(modified_code)
                tmp_path = tmp.name
            try:
                result = subprocess.run(
                    [sys.executable, tmp_path],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                output = result.stdout
                error = result.stderr
                self.update_status("Код выполнен")
            except subprocess.TimeoutExpired:
                output = ""
                error = "Превышено время выполнения (10 секунд)"
                self.update_status("Превышено время выполнения")
            except Exception as e:
                output = ""
                error = str(e)
                self.update_status("Ошибка выполнения")
            finally:
                try:
                    os.unlink(tmp_path)
                except:
                    pass

            output_win = ctk.CTkToplevel(self)
            output_win.title("Результат выполнения")
            output_win.geometry("600x400")
            output_text = ctk.CTkTextbox(output_win, font=('Consolas', self.font_size))
            output_text.pack(fill='both', expand=True)
            if output:
                output_text.insert('end', "--- ВЫВОД ---\n" + output + "\n")
            if error:
                output_text.insert('end', "--- ОШИБКИ ---\n" + error)
            if not output and not error:
                output_text.insert('end', "Программа выполнена без вывода и ошибок.")
            ctk.CTkButton(output_win, text="Закрыть", command=output_win.destroy).pack(pady=5)
        except Exception as e:
            messagebox.showerror("Ошибка запуска", f"Не удалось запустить код: {str(e)}")

    def handle_input_in_code(self, code):
        import re
        
        gui_imports = ['tkinter', 'customtkinter', 'ctk', 'tk', 'pygame', 'PyQt', 'PySide']
        for gui_import in gui_imports:
            if re.search(rf'\b(?:import|from)\s+{gui_import}\b', code):
                messagebox.showwarning("Внимание", 
                    f"Код содержит GUI-импорты ({gui_import}). Это может вызвать проблемы при выполнении.")
                return code
        
        # ИСПРАВЛЕННЫЙ паттерн: захватывает input() И все методы после него
        input_pattern = r'input\s*\([^)]*\)(?:\s*\.\s*\w+\s*\([^)]*\))*'
        matches = list(re.finditer(input_pattern, code))
        
        if not matches:
            return code
        
        user_inputs = []
        
        for match in matches:
            full_match = match.group(0)
            prompt_match = re.search(r'input\s*\(\s*["\']([^"\']*)["\']', full_match)
            prompt = prompt_match.group(1) if prompt_match else "Введите значение"
            
            user_value = self.custom_input(prompt)
            if user_value is None:
                return None
            
            user_inputs.append(user_value)
        
        # Заменяем ВЕСЬ найденный фрагмент на значение в кавычках
        modified_code = code
        for i, match in enumerate(reversed(matches)):
            start, end = match.span()
            user_value = user_inputs[len(matches)-1-i]
            
            escaped_value = user_value.replace('"', r'\"').replace("'", r"\'")
            replacement = f'"{escaped_value}"'
            
            modified_code = modified_code[:start] + replacement + modified_code[end:]
        
        return modified_code









    # ------------------------- ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ -------------------------
    def get_current_text_area(self):
        current_tab_id = self.notebook.select()
        if not current_tab_id:
            return None
        current_tab = self.nametowidget(current_tab_id)
        if current_tab in self.tabs:
            return self.tabs[current_tab]['text_area']
        return None

    def new_file(self):
        self.create_new_tab()

    def open_file(self):
        file_path = filedialog.askopenfilename()
        if file_path:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                self.create_new_tab(content, file_path)
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось открыть файл: {e}")

    def save_file(self):
        current_tab_id = self.notebook.select()
        if not current_tab_id:
            return
        current_tab = self.nametowidget(current_tab_id)
        if current_tab not in self.tabs:
            return
        text_area = self.tabs[current_tab]['text_area']
        filename = self.tabs[current_tab]['filename']
        content = text_area.get('1.0', tk.END)
        if filename:
            try:
                with open(filename, 'w', encoding='utf-8') as f:
                    f.write(content)
                self.update_status("Файл сохранён")
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось сохранить файл: {e}")
        else:
            file_path = filedialog.asksaveasfilename(
                defaultextension=".py",
                filetypes=[("Python файлы", "*.py"), ("Все файлы", "*.*")]
            )
            if file_path:
                try:
                    with open(file_path, 'w', encoding='utf-8') as f:
                        f.write(content)
                    self.tabs[current_tab]['filename'] = file_path
                    tab_name = os.path.basename(file_path)
                    tab_index = self.notebook.index(current_tab)
                    self.notebook.tab(tab_index, text=tab_name)
                    self.update_status("Файл сохранён")
                except Exception as e:
                    messagebox.showerror("Ошибка", f"Не удалось сохранить файл: {e}")

    # ------------------------- ПОДСВЕТКА СИНТАКСИСА ---------------------------
    def setup_syntax_highlighting_for_tab(self, text_area):
        # Профессиональная цветовая схема (в стиле Dracula/One Dark)
        text_area.tag_configure('keyword', foreground='#FF79C6', font=('Consolas', self.font_size, 'bold'))      # Розовый для ключевых слов
        text_area.tag_configure('string', foreground='#A9DC76')         # Светло-зеленый для строк
        text_area.tag_configure('comment', foreground='#6272A4', font=('Consolas', self.font_size, 'italic'))    # Приглушенный голубой для комментариев
        text_area.tag_configure('function_def', foreground='#78DCE8', font=('Consolas', self.font_size, 'bold')) # Голубой для определений функций
        text_area.tag_configure('function_call', foreground='#78DCE8')   # Голубой для вызовов функций
        text_area.tag_configure('number', foreground='#BD93F9')          # Фиолетовый для чисел
        text_area.tag_configure('operator', foreground='#FF79C6')        # Розовый для операторов
        text_area.tag_configure('builtin', foreground='#50FA7B')         # Зеленый для встроенных функций
        text_area.tag_configure('class_def', foreground='#F1FA8C', font=('Consolas', self.font_size, 'bold'))    # Желтый для классов
        text_area.tag_configure('decorator', foreground='#50FA7B')       # Зеленый для декораторов
        text_area.tag_configure('self', foreground='#FF79C6', font=('Consolas', self.font_size, 'italic'))       # Розовый курсив для self
        text_area.tag_configure('import_module', foreground='#F1FA8C')   # Желтый для импортированных модулей
        
        # Привязываем подсветку к событиям
        text_area.bind('<KeyRelease>', lambda e: self.after_idle(lambda: self.highlight_syntax_for_tab(text_area)))
        text_area.bind('<Button-1>', lambda e: self.after(1, lambda: self.highlight_syntax_for_tab(text_area)))
        
        # Начальная подсветка
        self.highlight_syntax_for_tab(text_area)

    def highlight_syntax_for_tab(self, text_area):
        try:
            # Получаем весь текст
            content = text_area.get('1.0', 'end-1c')
            
            # Удаляем все существующие теги
            for tag in ['keyword', 'string', 'comment', 'function_def', 'function_call', 'number', 
                       'operator', 'builtin', 'class_def', 'decorator', 'self', 'import_module']:
                text_area.tag_remove(tag, '1.0', 'end')
            
            # Python ключевые слова
            keywords = [
                'False', 'None', 'True', 'and', 'as', 'assert', 'async', 'await',
                'break', 'class', 'continue', 'def', 'del', 'elif', 'else', 'except',
                'finally', 'for', 'from', 'global', 'if', 'import', 'in', 'is', 'lambda',
                'nonlocal', 'not', 'or', 'pass', 'raise', 'return', 'try', 'while', 'with', 'yield'
            ]
            
            # Встроенные функции Python
            builtins = [
                'abs', 'all', 'any', 'bin', 'bool', 'bytearray', 'bytes', 'callable', 'chr',
                'classmethod', 'compile', 'complex', 'delattr', 'dict', 'dir', 'divmod',
                'enumerate', 'eval', 'exec', 'filter', 'float', 'format', 'frozenset',
                'getattr', 'globals', 'hasattr', 'hash', 'help', 'hex', 'id', 'input',
                'int', 'isinstance', 'issubclass', 'iter', 'len', 'list', 'locals',
                'map', 'max', 'memoryview', 'min', 'next', 'object', 'oct', 'open',
                'ord', 'pow', 'print', 'property', 'range', 'repr', 'reversed', 'round',
                'set', 'setattr', 'slice', 'sorted', 'staticmethod', 'str', 'sum', 'super',
                'tuple', 'type', 'vars', 'zip', '__import__'
            ]
            
            # 1. КОММЕНТАРИИ (самый высокий приоритет)
            self._highlight_pattern(text_area, r'#.*$', 'comment')
            
            # 2. СТРОКИ (тройные кавычки, двойные, одинарные, f-strings)
            self._highlight_pattern(text_area, r'f?"""[\s\S]*?"""', 'string')
            self._highlight_pattern(text_area, r"f?'''[\s\S]*?'''", 'string')
            self._highlight_pattern(text_area, r'f?"[^"\\]*(?:\\.[^"\\]*)*"', 'string')
            self._highlight_pattern(text_area, r"f?'[^'\\]*(?:\\.[^'\\]*)*'", 'string')
            
            # 3. ЧИСЛА
            self._highlight_pattern(text_area, r'\b\d+\.?\d*([eE][+-]?\d+)?\b', 'number')
            self._highlight_pattern(text_area, r'\b0[xX][0-9a-fA-F]+\b', 'number')
            self._highlight_pattern(text_area, r'\b0[oO][0-7]+\b', 'number')
            self._highlight_pattern(text_area, r'\b0[bB][01]+\b', 'number')
            
            # 4. ДЕКОРАТОРЫ
            self._highlight_pattern(text_area, r'@\w+', 'decorator')
            
            # 5. ИМПОРТЫ (модули после import/from)
            self._highlight_pattern(text_area, r'(?:from\s+)(\w+(?:\.\w+)*)', 'import_module', group=1)
            self._highlight_pattern(text_area, r'(?:import\s+)(\w+(?:\.\w+)*)', 'import_module', group=1)
            
            # 6. ОПРЕДЕЛЕНИЯ КЛАССОВ
            self._highlight_pattern(text_area, r'\bclass\s+(\w+)', 'class_def', group=1)
            
            # 7. ОПРЕДЕЛЕНИЯ ФУНКЦИЙ
            self._highlight_pattern(text_area, r'\bdef\s+(\w+)', 'function_def', group=1)
            
            # 8. SELF
            self._highlight_pattern(text_area, r'\bself\b', 'self')
            
            # 9. ВЫЗОВЫ ФУНКЦИЙ (имя перед скобкой, но не если это ключевое слово)
            for match in re.finditer(r'\b(\w+)(?=\s*\()', content):
                func_name = match.group(1)
                if func_name not in keywords and func_name != 'self':
                    start_idx = match.start(1)
                    end_idx = match.end(1)
                    start_pos, end_pos = self._get_tk_positions(content, start_idx, end_idx)
                    if not self._is_in_comment_or_string(text_area, start_pos):
                        text_area.tag_add('function_call', start_pos, end_pos)
            
            # 10. ВСТРОЕННЫЕ ФУНКЦИИ
            for builtin in builtins:
                self._highlight_pattern(text_area, r'\b' + re.escape(builtin) + r'\b', 'builtin')
            
            # 11. КЛЮЧЕВЫЕ СЛОВА
            for keyword in keywords:
                self._highlight_pattern(text_area, r'\b' + re.escape(keyword) + r'\b', 'keyword')
            
            # 12. ОПЕРАТОРЫ
            operators = [r'\+', r'-', r'\*', r'/', r'//', r'%', r'\*\*', r'==', r'!=', 
                        r'<=', r'>=', r'<', r'>', r'&', r'\|', r'\^', r'~', r'<<', r'>>', r'=']
            for op in operators:
                self._highlight_pattern(text_area, op, 'operator')
                
        except Exception as e:
            # Если что-то пошло не так, просто пропускаем подсветку
            pass

    def _highlight_pattern(self, text_area, pattern, tag, group=0):
        """Вспомогательная функция для подсветки по регулярному выражению"""
        try:
            content = text_area.get('1.0', 'end-1c')
            
            for match in re.finditer(pattern, content, re.MULTILINE):
                start_idx = match.start(group)
                end_idx = match.end(group)
                
                start_pos, end_pos = self._get_tk_positions(content, start_idx, end_idx)
                
                # Проверяем, не находится ли в комментарии или строке (кроме самих комментариев и строк)
                if tag in ['comment', 'string'] or not self._is_in_comment_or_string(text_area, start_pos):
                    text_area.tag_add(tag, start_pos, end_pos)
        except:
            pass

    def _get_tk_positions(self, content, start_idx, end_idx):
        """Преобразует индексы строки в позиции tkinter"""
        # Подсчитываем строки и символы до начальной позиции
        lines_before = content[:start_idx].count('\n')
        last_newline = content[:start_idx].rfind('\n')
        chars_before = start_idx - last_newline - 1 if last_newline != -1 else start_idx
        
        start_pos = f"{lines_before + 1}.{chars_before}"
        
        # Аналогично для конечной позиции
        lines_before_end = content[:end_idx].count('\n')
        last_newline_end = content[:end_idx].rfind('\n')
        chars_before_end = end_idx - last_newline_end - 1 if last_newline_end != -1 else end_idx
        
        end_pos = f"{lines_before_end + 1}.{chars_before_end}"
        
        return start_pos, end_pos

    def _is_in_comment_or_string(self, text_area, pos):
        """Проверяет, находится ли позиция внутри комментария или строки"""
        try:
            # Проверяем теги comment и string
            for tag in ['comment', 'string']:
                ranges = text_area.tag_ranges(tag)
                for i in range(0, len(ranges), 2):
                    if (text_area.compare(ranges[i], '<=', pos) and 
                        text_area.compare(pos, '<', ranges[i+1])):
                        return True
        except:
            pass
        return False

    # ------------------------- AI-ИНТЕГРАЦИЯ (ОПТИМИЗИРОВАНО) ------------------
    def setup_ai_integration(self):
        self.ai_history = []

    def send_query(self, event=None):
        user_text = self.user_input.get().strip()
        if not user_text:
            return

        self.user_input.delete(0, 'end')
        self.update_status("Отправляется запрос к AI...")

        # Вставляем сообщение пользователя
        self.chat_history.configure(state="normal")
        self.chat_history.insert('end', f"> {user_text}\n")
        self.chat_history.configure(state="disabled")
        self.chat_history.see('end')

        def worker():
            try:
                response = client.query(user_text)
                self.update_status("Ответ получен")
            except PerplexityClientError as e:
                response = f"Ошибка: {e}"
                self.update_status("Ошибка AI")

            # Вставляем ответ ИИ
            self.chat_history.configure(state="normal")
            self.chat_history.insert('end', f"{response}\n\n")
            self.chat_history.configure(state="disabled")
            self.chat_history.see('end')

        threading.Thread(target=worker, daemon=True).start()


    # ------------------------- КОПИРОВАНИЕ/ВСТАВКА -----------------------------
    def copy_text(self):
        text_area = self.get_current_text_area()
        if text_area:
            try:
                selected = text_area.get("sel.first", "sel.last")
                self.clipboard_clear()
                self.clipboard_append(selected)
                self.update_status("Текст скопирован")
            except tk.TclError:
                pass

    def paste_text(self):
        text_area = self.get_current_text_area()
        if text_area:
            try:
                text_area.insert("insert", self.clipboard_get())
                self.update_status("Текст вставлен")
            except tk.TclError:
                pass

# --------------------------- ЗАПУСК ПРИЛОЖЕНИЯ --------------------------------
if __name__ == "__main__":
    editor = CodeEditor()
    editor.mainloop()
