# START SECTION 1: Imports and Constants
import sys
import hashlib  # Add to imports
from functools import partial
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QVBoxLayout, QHBoxLayout, QWidget,
    QPushButton, QLineEdit, QLabel, QListWidget, QListWidgetItem,
    QInputDialog, QMessageBox, QMenu, QGridLayout, QDialog,
    QDialogButtonBox, QScrollArea, QCheckBox, QSpinBox, QColorDialog,
    QComboBox, QSizePolicy, QProgressDialog, QAction, QFileDialog
)
from PyQt5.QtCore import Qt, QTimer, QPoint, QSize, QThread, pyqtSignal
from PyQt5.QtGui import QColor, QCursor

import json # Already present, will be used more
import serial
import time
import socket

UDP_DISCOVERY_PORT = 4210
DISCOVERY_PACKET = b"PYRO_DISCOVERY_REQUEST" # Note the 'b' for bytes
DISCOVERY_REPLY = "I_AM_PYRO_CONTROLLER"

ESP32_IP = "192.168.0.156"  # <<< CHANGE THIS TO THE IP FROM ARDUINO SERIAL MONITOR
ENABLE_WIFI = True # Use this as our main flag now

# New constants for JSON communication
JSON_SEQUENCE_START_CMD = "UPLOAD_JSON_START"
JSON_SEQUENCE_END_CMD = "END_JSON_PAYLOAD" # To mark the end of a potentially large JSON string
# END SECTION 1: Imports and Constants
# START SECTION 2: SerialWorker Class
class SerialWorker(QThread):
    """
    Handles serial communication in a separate thread to prevent blocking the GUI.
    """
    # Signals emitted by the worker thread
    data_received = pyqtSignal(str)      # Emits incoming data lines from ESP32
    connection_status = pyqtSignal(bool) # Emits True for connected, False for disconnected/error
    # sequence_upload_status signal is removed as status is handled by MainWindow based on ACKs

    def __init__(self, port, baudrate=115200):
        super().__init__()
        self.port = port
        self.baudrate = baudrate
        self.serial_connection = None
        self.running = True          # Flag to control the run loop
        print(f"SerialWorker initialized for port {self.port} @ {self.baudrate} baud.")

    def run(self):
        """The main loop for the serial worker thread."""
        print(f"SerialWorker thread starting for {self.port}...")
        try:
            # Attempt to establish serial connection
            self.serial_connection = serial.Serial(self.port, self.baudrate, timeout=1)
            print(f"Serial port {self.port} opened successfully.")
            self.connection_status.emit(True)  # Signal successful connection

            # Main reading loop
            while self.running:
                if self.serial_connection is None or not self.serial_connection.is_open:
                     print("Serial connection lost unexpectedly in run loop.")
                     self.connection_status.emit(False)
                     break # Exit loop if connection is lost

                # Check if data is available to read
                if self.serial_connection.in_waiting > 0:
                    try:
                        # Read a line, decode, and strip whitespace
                        line = self.serial_connection.readline().decode('utf-8', errors='ignore').rstrip()
                        if line: # Only emit non-empty lines
                           self.data_received.emit(line)
                    except UnicodeDecodeError:
                         print("Warning: SerialWorker encountered UnicodeDecodeError. Ignoring byte.")
                    except serial.SerialException as read_error:
                         print(f"Serial read error in run loop: {read_error}")
                         self.connection_status.emit(False)
                         break # Exit loop on read error
                else:
                    # Small sleep to prevent high CPU usage when idle
                    time.sleep(0.02) # 20ms sleep

        except serial.SerialException as e:
            # Handle errors during initial connection attempt
            print(f"Serial connection error on port {self.port}: {e}")
            self.connection_status.emit(False)
        except Exception as e:
            # Handle unexpected errors in the thread
            print(f"An unexpected error occurred in SerialWorker run loop: {e}")
            self.connection_status.emit(False)
        finally:
            # Cleanup: Ensure the serial port is closed when the thread stops
            if self.serial_connection and self.serial_connection.is_open:
                self.serial_connection.close()
                print(f"Serial port {self.port} closed.")
            # Signal final disconnection status
            self.connection_status.emit(False)
            print(f"SerialWorker thread for {self.port} finished.")

    def stop(self):
        """Signals the worker thread to stop."""
        print(f"Stopping SerialWorker thread for {self.port}...")
        self.running = False
        self.wait(2000) # Wait up to 2 seconds for the thread to finish gracefully

    def send_data(self, data):
        """
        Thread-safe method to send data to the ESP32.
        Encodes to UTF-8. The CALLER is responsible for adding newlines.
        Returns True on successful write attempt, False otherwise.
        """
        try:
            if not self.isRunning() or not self.serial_connection:
                print("Serial worker not running or connection invalid. Cannot send.")
                return False

            if not self.serial_connection.is_open:
                print("Serial connection closed unexpectedly. Cannot send.")
                self.connection_status.emit(False)
                return False

            # *** CHANGE IS HERE: We no longer add a newline automatically. ***
            # The calling function must now provide it.
            data_to_send = data

            # Perform the write operation
            self.serial_connection.write(data_to_send.encode('utf-8'))
            self.serial_connection.flush()
            # print(f"Sent RAW: {data_to_send}") # Optional: Debug print for sent data
            return True

        except serial.SerialTimeoutException:
            print(f"Error sending data: Serial Timeout on port {self.port}")
            self.connection_status.emit(False)
            return False
        except serial.SerialException as e:
            print(f"Error sending data: Serial Exception on port {self.port}: {e}")
            self.connection_status.emit(False)
            return False
        except Exception as e:
            print(f"Error sending data: Unexpected error: {e}")
            return False

# END SECTION 2: SerialWorker Class


# END SECTION 2: SerialWorker Class


# START SECTION 2.5: WiFiWorker Class
import socket

class WiFiWorker(QThread):
    """
    Handles TCP communication over Wi-Fi in a separate thread.
    Mimics the signals and methods of SerialWorker.
    """
    data_received = pyqtSignal(str)
    connection_status = pyqtSignal(bool)

    def __init__(self, host, port=8080):
        super().__init__()
        self.host = host
        self.port = port
        self.sock = None
        self.running = True
        self.recv_buffer = b''
        print(f"WiFiWorker initialized for host {self.host}:{self.port}.")

    def run(self):
        print(f"WiFiWorker thread starting for {self.host}...")
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(5.0)
            self.sock.connect((self.host, self.port))
            self.sock.settimeout(1.0)
            print(f"Successfully connected to {self.host}:{self.port}.")
            self.connection_status.emit(True)

            while self.running:
                try:
                    # <<< VERBOSE LOGGING START >>>
                    data = self.sock.recv(1024)
                    if not data:
                        print("!!! PYTHON: WiFi connection closed by the server.")
                        break
                    
                    if data:
                        print(f">>> PYTHON RX: Received raw bytes: {data}")

                    self.recv_buffer += data
                    while b'\n' in self.recv_buffer:
                        line, self.recv_buffer = self.recv_buffer.split(b'\n', 1)
                        line_str = line.decode('utf-8', errors='ignore').strip()
                        if line_str:
                            print(f">>> PYTHON RX: Processing line: '{line_str}'")
                            self.data_received.emit(line_str)
                    # <<< VERBOSE LOGGING END >>>

                except socket.timeout:
                    continue
                except Exception as e:
                    print(f"!!! PYTHON: Error during WiFi receive loop: {e}")
                    break
        
        except socket.error as e:
            print(f"!!! PYTHON: WiFi connection error: {e}")
            self.connection_status.emit(False)
        finally:
            if self.sock:
                self.sock.close()
            self.connection_status.emit(False)
            print("--- PYTHON: WiFiWorker thread finished. ---")

    def stop(self):
        print("--- PYTHON: Stopping WiFiWorker thread... ---")
        self.running = False
        self.wait(2000)

    def send_data(self, data):
        if not self.running or not self.sock:
            print("!!! PYTHON: WiFi worker not running or socket invalid. Cannot send.")
            return False
        try:
            # <<< VERBOSE LOGGING >>>
            print(f"<<< PYTHON TX: Sending data: {data.encode('utf-8')}")
            self.sock.sendall(data.encode('utf-8'))
            return True
        except socket.error as e:
            print(f"!!! PYTHON: Error sending WiFi data: {e}")
            self.connection_status.emit(False)
            return False



# END SECTION 2.5: WiFiWorker Class


# START SECTION 3: CodeEditor Class
class CodeEditor:
    def __init__(self):
        self.history = []
        self.future = []
        self.configurations = {}

    def add_change(self, change):
        self.history.append(change)
        self.future.clear()

    def undo(self):
        if self.history:
            change = self.history.pop()
            self.future.append(change)

    def redo(self):
        if self.future:
            change = self.future.pop()
            self.history.append(change)

    def save_configuration(self, filename="config.json"):
        with open(filename, "w") as f:
            json.dump(self.configurations, f)

    def load_configuration(self, filename="config.json"):
        try:
            with open(filename, "r") as f:
                self.configurations = json.load(f)
        except FileNotFoundError:
            print("Configuration file not found.")
# END SECTION 3: CodeEditor Class


# START SECTION 4: DraggableLED Class
class DraggableLED(QLabel):
    def __init__(self, text, parent=None, main_window=None):
        super().__init__(parent)
        self.main_window = main_window
        self.setFixedSize(80, 80)
        # Default style, can be overridden by group color
        self.setStyleSheet("""
            background-color: #666;
            border-radius: 40px;
            border: 2px solid #999;
            color: white;
            font-size: 24px;
        """)
        self.setAlignment(Qt.AlignCenter)
        self.setText(text)
        self.color = QColor(100, 100, 100) # Default unassigned color
        self.draggable = True
        self.drag_start_pos = QPoint()
        self.group = None
        # self.main_window = main_window # Already assigned

    def set_color(self, color):
        self.color = color
        self.update_style()

    def update_style(self):
        # Check if this LED belongs to a pause group, if so, use a fixed "unassigned" style
        is_pause_member = False
        if self.group and self.main_window:
            group_data = self.main_window.get_group_by_name(self.group)
            if group_data and group_data.get("pattern") == "Pause":
                is_pause_member = True # Should not happen as pause groups have no members

        # If part of a pause group (logically shouldn't be assigned a color directly this way)
        # or if no group, use default. Otherwise, use the set color.
        # This logic might be redundant if pause groups never have DraggableLEDs assigned.
        # The primary color setting for LEDs happens in edit_group_members.
        # Pause groups won't call set_color on LEDs.
        # This is more of a safeguard if something tried to assign a color.
        # For LEDs that are *unassigned* and become part of a *new* group,
        # their color is set by `edit_group_members`.
        # This method is more for when a group's color *changes*.

        bg_color_name = self.color.name()
        # If an LED is unassigned (self.group is None), its self.color is the default gray.
        # If an LED is assigned to a regular group, self.color is the group's color.
        # Pause groups don't have members, so an LED shouldn't have self.group pointing to a pause group.

        self.setStyleSheet(f"""
            background-color: {bg_color_name};
            border-radius: 40px;
            border: 2px solid #999;
            color: white;
            font-size: 24px;
        """)


    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            menu = QMenu()
            group_name_display = "Unassigned"
            is_pause_group_member = False # LEDs shouldn't be members of pause groups
            current_group_info = "Unassigned"

            if self.group and self.main_window:
                group_data = self.main_window.get_group_by_name(self.group)
                if group_data:
                    group_name_display = group_data["name"]
                    if group_data.get("pattern") == "Pause":
                        # This case should ideally not happen as pause groups don't have members.
                        # If it did, it means an LED was somehow assigned to a pause group.
                        current_group_info = f"Group: {group_name_display} (Pause - No Members)"
                        is_pause_group_member = True
                    else:
                        current_group_info = f"Group: {group_name_display}"

            menu.addAction(current_group_info).setEnabled(False)
            menu.exec_(QCursor.pos())
        elif self.draggable and event.button() == Qt.LeftButton:
            self.drag_start_pos = event.pos()

    def mouseMoveEvent(self, event):
        if self.draggable and event.buttons() == Qt.LeftButton:
            # Parent container for dragging. LEDs are children of led_container,
            # even if a QGridLayout is set on led_container.
            # self.move() will be relative to led_container.
            target_container = self.main_window.led_container

            # self.drag_start_pos is the offset from the widget's top-left corner
            # to where the mouse was initially pressed (local to the widget).

            # QCursor.pos() gives the current global (screen) coordinates of the mouse.
            # To find the new top-left corner of our widget in global coordinates:
            # new_widget_global_top_left = current_global_mouse_pos - self.drag_start_pos
            new_widget_global_top_left = QCursor.pos() - self.drag_start_pos
            
            # Now, map this global top-left position to be relative to our target_container
            new_pos_in_container = target_container.mapFromGlobal(new_widget_global_top_left)

            # Constrain the new position within the bounds of the target_container
            padding = 2 # A small padding from the edges
            
            constrained_x = max(padding, 
                                min(new_pos_in_container.x(), 
                                    target_container.width() - self.width() - padding))
            constrained_y = max(padding, 
                                min(new_pos_in_container.y(), 
                                    target_container.height() - self.height() - padding))
            
            # Move the widget. Since DraggableLED instances are direct children of
            # self.led_container (where self.grid is applied), this move should
            # be relative to self.led_container.
            self.move(QPoint(constrained_x, constrained_y))
            # Note: QGridLayout might still try to impose its will if the window is resized
            # or other layout updates occur. For the duration of the drag, this manual
            # positioning takes precedence.


    def activate(self):
        """Flash LED while maintaining group color reference"""
        # For LEDs, activation color is always red, then back to its assigned/default color.
        # The original color is stored in self.color
        original_bg_color = self.color.name()

        self.setStyleSheet(f"""
            background-color: #ff0000;
            border-radius: 40px;
            border: 2px solid #fff;
            color: white;
            font-size: 24px;
        """)
        QTimer.singleShot(300, lambda: self.setStyleSheet(f"""
            background-color: {original_bg_color};
            border-radius: 40px;
            border: 2px solid #999;
            color: white;
            font-size: 24px;
        """))

    def get_group_color(self):
        """Get current color from group settings. Returns default if unassigned or pause group."""
        if self.group and self.main_window:
            group_data = self.main_window.get_group_by_name(self.group)
            if group_data and group_data.get("pattern") != "Pause": # Pause groups don't color LEDs
                return group_data.get("color", QColor(100, 100, 100))
        return QColor(100, 100, 100) # Default gray
# END SECTION 4: DraggableLED Class

# START SECTION 5: GroupHeaderWidget Class
class GroupHeaderWidget(QWidget):
    # def __init__(self, name, color, pattern, main_window, parent=None): # Old signature
    def __init__(self, group_data, main_window, parent=None): # New signature taking full group_data
        super().__init__(parent)
        self.main_window = main_window
        self.group_name = group_data["name"] # Store name for easy access

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 5)
        layout.setSpacing(15)

        self.color_indicator = QLabel()
        self.color_indicator.setFixedSize(30, 30)
        group_color = group_data.get("color", QColor(50,50,50)) # Default for pause or if missing
        self.color_indicator.setStyleSheet(f"""
            background-color: {group_color.name()};
            border-radius: 15px;
            border: 2px solid #fff;
        """)

        self.name_label = QLabel(self.group_name)
        self.name_label.setStyleSheet("""
            font-size: 22px;
            font-weight: bold;
            color: white;
            padding: 5px;
        """)
        self.name_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        self.pattern_label = QLabel() # Text set below
        self.pattern_label.setStyleSheet("""
            font-size: 18px;
            color: #CCCCCC;
            padding: 5px;
        """)
        self.update_pattern_display(group_data) # Call helper to set pattern/pause text


        self.menu_button = QPushButton("⚙️")
        self.menu_button.setFixedSize(40, 40)
        self.menu_button.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 255, 255, 20);
                border-radius: 20px;
                color: white;
                font-size: 20px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 50);
            }
        """)

        layout.addWidget(self.color_indicator)
        layout.addWidget(self.name_label)
        layout.addWidget(self.pattern_label)
        layout.addWidget(self.menu_button)

        self.menu_button.clicked.connect(self.show_menu)

    def update_pattern_display(self, group_data):
        """Sets the text for the pattern_label based on group type."""
        pattern = group_data.get("pattern")
        if pattern == "Pause":
            gap_ms = group_data.get("gap", 0)
            self.pattern_label.setText(f"Pause: {gap_ms}ms")
        elif pattern and pattern != "None":
            self.pattern_label.setText(f"Pattern: {pattern}")
        else:
            self.pattern_label.setText("Pattern: None")


    def show_menu(self):
        menu = QMenu()
        # Fetch fresh group data in case it changed (e.g. name)
        current_group_data = self.main_window.get_group_by_name(self.group_name)
        if not current_group_data:
            QMessageBox.warning(self.main_window, "Error", f"Group '{self.group_name}' no longer exists.")
            return

        is_pause_group = current_group_data.get("pattern") == "Pause"

        if is_pause_group:
            rename_action = menu.addAction("✏️ Rename Pause")
            edit_duration_action = menu.addAction("⏱️ Edit Duration")
            # color_action = menu.addAction("🎨 Change Color") # Pause groups might have a fixed color
            delete_action = menu.addAction("❌ Delete Pause")
        else: # Regular group
            rename_action = menu.addAction("✏️ Rename Group")
            edit_action = menu.addAction("🔧 Edit Members")
            pattern_action = menu.addAction("🎆 Select Pattern")
            color_action = menu.addAction("🎨 Change Color")
            delete_action = menu.addAction("❌ Delete Group")

        action = menu.exec_(self.menu_button.mapToGlobal(QPoint(0, self.menu_button.height())))

        # Use self.group_name which was stored at __init__ as the identifier
        if action == rename_action:
            self.main_window.rename_group(self.group_name)
        elif is_pause_group and action == edit_duration_action:
            self.main_window.edit_pause_duration(self.group_name)
        elif not is_pause_group and action == edit_action:
            self.main_window.edit_group_members(self.group_name)
        elif not is_pause_group and action == pattern_action:
            self.main_window.select_pattern(self.group_name)
        elif not is_pause_group and action == color_action: # Only allow color change for non-pause
            self.main_window.change_group_color(self.group_name)
        elif action == delete_action:
            self.main_window.delete_group(self.group_name)
# END SECTION 5: GroupHeaderWidget Class


# START SECTION 6: MemberEditDialog Class
class MemberEditDialog(QDialog):
    def __init__(self, leds_count, existing_members, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.existing_members = existing_members
        self.setWindowTitle("Edit Group Members")
        self.setStyleSheet("background-color: #2D2D2D; color: #FFFFFF;")
        self.setMinimumSize(600, 700)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(15)
        
        # --- FORCEFUL FONT SIZES ---
        main_layout.addWidget(QLabel("Add by Range:", self, styleSheet="font-size: 16px;"))
        self.range_input = QLineEdit()
        self.range_input.setPlaceholderText("e.g., 1-8, 12-15")
        self.range_input.setStyleSheet("font-size: 16px; padding: 10px; background-color: #404040; border-radius: 5px;")
        main_layout.addWidget(self.range_input)
        
        main_layout.addWidget(QLabel("Add Individual Cues:", self, styleSheet="font-size: 16px;"))
        self.individual_input = QLineEdit()
        self.individual_input.setPlaceholderText("e.g., 1, 5, 11")
        self.individual_input.setStyleSheet("font-size: 16px; padding: 10px; background-color: #404040; border-radius: 5px;")
        main_layout.addWidget(self.individual_input)
        
        main_layout.addSpacing(10)
        main_layout.addWidget(QLabel("Select Cues Manually:", self, styleSheet="font-size: 16px;"))

        # --- Checkbox area setup ... ---
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setStyleSheet("background-color: #1E1E1E; border-radius: 5px;")
        
        scroll_content_widget = QWidget()
        self.grid_layout = QGridLayout(scroll_content_widget)
        self.grid_layout.setSpacing(10)
        self.grid_layout.setContentsMargins(15, 15, 15, 15)

        self.checkboxes = []
        num_columns = 5
        for i in range(leds_count):
            cue_num = i + 1
            cb = QCheckBox(f"{cue_num}")
            
            # --- MAKE CHECKBOXES BIGGER AND SET FONT ---
            cb.setStyleSheet("""
                QCheckBox { font-size: 16px; spacing: 10px; }
                QCheckBox::indicator { width: 22px; height: 22px; }
            """)
            
            if i in self.existing_members:
                cb.setChecked(True)

            is_in_other_group = any(i in g.get("members", []) and g.get("members") is not self.existing_members for g in self.main_window.groups)
            
            if is_in_other_group:
                cb.setText(f"{cue_num} (in use)")
                cb.setStyleSheet(cb.styleSheet() + "color: #FF6B6B;") # Append to existing style
                cb.setEnabled(False)

            self.checkboxes.append(cb)
            self.grid_layout.addWidget(cb, i // num_columns, i % num_columns)

        scroll_area.setWidget(scroll_content_widget)
        main_layout.addWidget(scroll_area)

        # --- Button Box ---
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.setStyleSheet("QPushButton { font-size: 16px; padding: 10px 25px; }")
        button_box.accepted.connect(self.validate_and_accept)
        button_box.rejected.connect(self.reject)
        main_layout.addWidget(button_box)

    def get_selected(self):
        # This function is now simplified and more robust
        selected_indices = set()

        # 1. Get from checkboxes
        for i, cb in enumerate(self.checkboxes):
            if cb.isChecked():
                selected_indices.add(i)

        # 2. Get from range input
        try:
            # Allow multiple comma-separated ranges, e.g., "1-8, 12-15"
            ranges = self.range_input.text().split(',')
            for r in ranges:
                r = r.strip()
                if '-' in r:
                    start, end = map(int, r.split('-'))
                    for i in range(start, end + 1):
                        if 0 <= i - 1 < len(self.checkboxes):
                            selected_indices.add(i - 1)
        except ValueError:
            # Ignore malformed range input
            pass 

        # 3. Get from individual input
        try:
            # Allow multiple comma-separated numbers
            nums = self.individual_input.text().split(',')
            for n in nums:
                n = n.strip()
                if n:
                    cue_num = int(n)
                    if 0 <= cue_num - 1 < len(self.checkboxes):
                        selected_indices.add(cue_num - 1)
        except ValueError:
            # Ignore malformed individual input
            pass
            
        return sorted(list(selected_indices))

    def validate_and_accept(self):
        """
        This is a simplified validation. Since we disable checkboxes for cues
        already in other groups, we no longer need the complex conflict resolution pop-up.
        We can simply accept the new selection.
        """
        self.accept()
        
# END SECTION 6: MemberEditDialog Class

# START SECTION 7: PatternDialog Class
class PatternDialog(QDialog):
    def __init__(self, patterns, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select Pattern")
        self.setStyleSheet("background-color: #2D2D2D; color: #FFFFFF;")
        self.setMinimumWidth(400) # Give it more space
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)
        
        # --- FORCEFUL FONT SIZES ---
        pattern_label = QLabel("Select Pattern:")
        pattern_label.setStyleSheet("font-size: 16px;")
        layout.addWidget(pattern_label)
        
        self.pattern_combo = QComboBox()
        self.pattern_combo.setStyleSheet("font-size: 16px; padding: 8px;")
        self.pattern_combo.addItem("None")
        self.pattern_combo.addItems(patterns)
        layout.addWidget(self.pattern_combo)
        
        layout.addSpacing(15) # Add extra space

        gap_label = QLabel("Delay Between Cues:")
        gap_label.setStyleSheet("font-size: 16px;")
        layout.addWidget(gap_label)

        self.gap_spin = QSpinBox()
        self.gap_spin.setStyleSheet("font-size: 16px; padding: 8px;")
        self.gap_spin.setRange(100, 5000) # Increased range
        self.gap_spin.setSuffix(" ms")
        self.gap_spin.setValue(500)
        layout.addWidget(self.gap_spin)

        layout.addStretch() # Push buttons to the bottom

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.setStyleSheet("QPushButton { font-size: 14px; padding: 8px 20px; }")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


        self.setLayout(layout)

# # In SECTION 7.5, CustomInputDialog.__init__
class CustomInputDialog(QDialog):
    def __init__(self, parent=None, title="Enter Value", label="Name:", initial_text=""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)

        self.label = QLabel(label, self)
        # --- FORCEFUL FONT SIZE ---
        self.label.setStyleSheet("font-size: 16px;")
        layout.addWidget(self.label)

        self.lineEdit = QLineEdit(self)
        self.lineEdit.setText(initial_text)
        # --- FORCEFUL FONT SIZE & PADDING ---
        self.lineEdit.setStyleSheet("font-size: 16px; padding: 8px;")
        layout.addWidget(self.lineEdit)

        self.buttonBox = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        # --- FORCEFUL FONT SIZE ---
        self.buttonBox.setStyleSheet("QPushButton { font-size: 14px; padding: 8px 20px; }")
        self.buttonBox.accepted.connect(self.accept)
        self.buttonBox.rejected.connect(self.reject)
        layout.addWidget(self.buttonBox)

    def get_text(self):
        # Helper function to get the text after the dialog is accepted
        return self.lineEdit.text()

# START SECTION 8: MainWindow Class - __init__ and initUI
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        # 1. Initialize all internal variables first
        self.groups = []
        self.leds = []
        self.patterns = [
            "Right Wave", "Left Wave", "Alternate",
            "Center Burst", "Inwards", "All Fire"
        ]
        self.current_highlight = None
        self.esp32_connected = False
        self.sequence_uploaded = False
        self.serial_worker = None
        
        # --- KEY CHANGE: Set the default mode ---
        self.software_only_mode = True 
        
        self.software_toggle = None
        self.esp32_status_widget = None
        # ... (other variable initializations)

        # --- Undo/Redo History Manager ---
        self.editor_history = CodeEditor()
        self.is_restoring_state = False # A flag to prevent loops

        # 2. Build the User Interface
        self.initUI()

        # 3. Synchronize the UI to match the initial state
        # This will set the button text/color to "Software Only"
        self.update_button_style() 
        # This will correctly enable/disable buttons for software mode
        self.update_button_states() 

        # 4. Now, check if we should connect to hardware
        # This 'if' block will now be skipped on initial startup because
        # self.software_only_mode is True.
        if ENABLE_WIFI and not self.software_only_mode:
            self.start_wireless_communication()

    def initUI(self):
        self.setWindowTitle("Pyro Control System")
        self.setWindowState(Qt.WindowFullScreen)
        
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        
        # Create the main layout ONCE at the beginning
        layout = QHBoxLayout(main_widget)
        layout.setContentsMargins(15, 15, 15, 15) 

        # --- Menu Bar ---
        menubar = self.menuBar()
        fileMenu = menubar.addMenu('&File')
        openAction = QAction('Open Sequence...', self)
        openAction.setShortcut('Ctrl+O')
        openAction.triggered.connect(self.open_sequence_from_file)
        fileMenu.addAction(openAction)
        saveAction = QAction('Save Sequence As...', self)
        saveAction.setShortcut('Ctrl+S')
        saveAction.triggered.connect(self.save_sequence_to_file)
        fileMenu.addAction(saveAction)
        editMenu = menubar.addMenu('&Edit')

        self.undoAction = QAction('Undo', self)
        self.undoAction.setShortcut('Ctrl+Z')
        self.undoAction.triggered.connect(self.undo_change)
        self.undoAction.setEnabled(False) # Start disabled
        editMenu.addAction(self.undoAction)

        self.redoAction = QAction('Redo', self)
        self.redoAction.setShortcut('Ctrl+Y') # or Ctrl+Shift+Z
        self.redoAction.triggered.connect(self.redo_change)
        self.redoAction.setEnabled(False) # Start disabled
        editMenu.addAction(self.redoAction)

        

        # *** THE DUPLICATE LINE HAS BEEN REMOVED FROM HERE ***

        # --- Left Panel ---
        left_panel = QVBoxLayout()
        left_panel.setSpacing(12)
        init_label = QLabel("Initialize System:")
        init_label.setStyleSheet("font-size: 18px; font-weight: bold; margin-bottom: 5px;")
        init_layout = QVBoxLayout()
        init_layout.addWidget(init_label)
        self.num_input = QLineEdit(placeholderText="Number of cues")
        button_row = QHBoxLayout()
        self.init_btn = QPushButton("🎇 Initialize")
        self.init_btn.clicked.connect(self.init_crackers)
        self.init_btn.setStyleSheet("font-size: 20px; padding: 12px; background-color: #4CAF50;") 
        self.reset_btn = QPushButton("🔁 Reset")
        self.reset_btn.clicked.connect(self.reset_program)
        self.reset_btn.setStyleSheet("font-size: 20px; padding: 12px; background-color: #FF5722;") 
        button_row.addWidget(self.init_btn)
        button_row.addWidget(self.reset_btn)
        init_layout.addWidget(self.num_input)
        init_layout.addLayout(button_row)
        left_panel.addLayout(init_layout)
        add_crackers_label = QLabel("Add More Cues:")
        add_crackers_label.setStyleSheet("font-size: 14px; font-weight: bold; margin-top: 10px; margin-bottom: 5px;") # Was 18px
        left_panel.addWidget(add_crackers_label)
        add_layout = QHBoxLayout()
        self.add_single_btn = QPushButton("➕ Add 1")
        self.add_single_btn.clicked.connect(partial(self.add_crackers_button_clicked, 1))
        self.add_five_btn = QPushButton("➕ Add 5")
        self.add_five_btn.clicked.connect(partial(self.add_crackers_button_clicked, 5))
        add_layout.addWidget(self.add_single_btn)
        add_layout.addWidget(self.add_five_btn)
        left_panel.addLayout(add_layout)
                # In SECTION 8, inside initUI()

        stats_label_qlabel = QLabel("System Stats:") 
        stats_label_qlabel.setStyleSheet("font-size: 16px; font-weight: bold; margin-top:10px; margin-bottom: 5px;")
        left_panel.addWidget(stats_label_qlabel)
        
        stats_layout = QGridLayout() 
        
        self.stats_label = QLabel("Cues: 0 | Groups: 0")
        # --- FORCEFUL FONT SIZE ---
        self.stats_label.setStyleSheet("font-size: 16px; color: #FFB74D;") 
        
        self.time_label = QLabel("Est. Time: 0s")
        # --- FORCEFUL FONT SIZE ---
        self.time_label.setStyleSheet("font-size: 16px; color: #81C784;") 
        
        self.countdown_label = QLabel("") 
        # --- FORCEFUL FONT SIZE ---
        self.countdown_label.setStyleSheet("font-size: 18px; color: #E57373; font-weight:bold;") 
        
        stats_layout.addWidget(self.stats_label, 0, 0)
        stats_layout.addWidget(self.time_label, 0, 1)
        stats_layout.addWidget(self.countdown_label, 1, 0, 1, 2, Qt.AlignCenter) 
        left_panel.addLayout(stats_layout)
        timeline_label = QLabel("Sequence Timeline:")
        timeline_label.setStyleSheet("font-size: 18px; font-weight: bold; margin-top:10px; margin-bottom: 5px;")
        left_panel.addWidget(timeline_label)
        self.timeline_list = QListWidget()
        left_panel.addWidget(self.timeline_list, 1) 
        self.esp32_status_widget = QWidget()
        self.esp32_status_layout = QVBoxLayout(self.esp32_status_widget)
        self.esp32_status_widget.setStyleSheet("background-color: #333333; border-radius: 8px; padding: 8px;") 
        self.connection_status_label = QLabel("ESP32: Not Connected")
        self.connection_status_label.setStyleSheet("font-size: 16px; color: #FF6B6B;")
        
        # --- ADD AUTO CONNECT BUTTON ---
        self.auto_connect_btn = QPushButton("🔍 Auto Connect")
        self.auto_connect_btn.setStyleSheet("background-color: #2196F3; color: white; font-weight: bold; padding: 8px;")
        self.auto_connect_btn.clicked.connect(self.perform_auto_discovery)
        
        # --- MODIFY REFRESH BUTTON ---
        self.refresh_btn = QPushButton("Manual Connect") # Renamed
        self.refresh_btn.clicked.connect(self.start_wireless_communication)
        
        self.esp32_status_layout.addWidget(self.connection_status_label)
        self.esp32_status_layout.addWidget(self.auto_connect_btn) # <--- ADDED
        self.esp32_status_layout.addWidget(self.refresh_btn)

        # RAIL STATUS (SAFETY MONITOR)
        self.rail_status_label = QLabel("RAIL: UNKNOWN")
        self.rail_status_label.setStyleSheet("font-size: 18px; font-weight: bold; color: grey; border: 2px solid grey; padding: 5px; border-radius: 5px; margin-top: 5px;")
        self.rail_status_label.setAlignment(Qt.AlignCenter)
        left_panel.addWidget(self.rail_status_label)
        # ----------------------

        self.sequence_status_label = QLabel("Sequence: Not Uploaded")

        # --- ADD RAIL STATUS LABEL ---
        self.rail_status_label = QLabel("RAIL: UNKNOWN")
        self.rail_status_label.setStyleSheet("font-size: 18px; font-weight: bold; color: grey; border: 2px solid grey; padding: 5px; border-radius: 5px; margin-top: 5px;")
        self.rail_status_label.setAlignment(Qt.AlignCenter)
        self.esp32_status_layout.addWidget(self.rail_status_label)

        self.sequence_status_label = QLabel("Sequence: Not Uploaded")
        self.sequence_status_label.setStyleSheet("font-size: 16px; color: #FF6B6B;") 
        self.esp32_status_layout.addWidget(self.sequence_status_label)


        self.esp32_status_widget.setFixedHeight(self.esp32_status_widget.sizeHint().height() + 10) 
        left_panel.addWidget(self.esp32_status_widget)

                # --- Right Panel ---
        right_panel = QVBoxLayout()
        right_panel.setSpacing(12)
        groups_label = QLabel("Groups & Pauses:")
        groups_label.setStyleSheet("font-size: 18px; font-weight: bold; margin-bottom: 5px;")
        right_panel.addWidget(groups_label)

        self.group_list = QListWidget()
        self.group_list.setStyleSheet("""
            QListWidget::item { min-height: 65px; border-bottom: 1px solid #383838; }
            QListWidget::item:selected { background-color: #4A4A4A; }
        """) 
        self.group_list.setDragDropMode(QListWidget.InternalMove)
        self.group_list.model().rowsMoved.connect(self.handle_group_reorder)
        right_panel.addWidget(self.group_list, 1) 

        # This is the main vertical layout for all the control buttons at the bottom
        group_controls_v_layout = QVBoxLayout()
        group_controls_v_layout.setSpacing(8) 

        # --- Row 1: Add Group / Add Pause ---
        group_btns_row1 = QHBoxLayout()
        self.add_group_btn = QPushButton("➕ Create Group")
        self.add_group_btn.clicked.connect(self.add_group)
        self.add_pause_btn = QPushButton("⏸️ Add Pause") 
        self.add_pause_btn.clicked.connect(self.add_pause_group)
        group_btns_row1.addWidget(self.add_group_btn)
        group_btns_row1.addWidget(self.add_pause_btn)
        group_controls_v_layout.addLayout(group_btns_row1)

        # --- Row 2: Upload / Start ---
        group_btns_row2 = QHBoxLayout()
        self.upload_sequence_btn = QPushButton("⬆️ Upload Sequence")
        self.upload_sequence_btn.clicked.connect(self.upload_sequence)
        self.start_btn = QPushButton("🚀 Start Sequence")
        self.start_btn.clicked.connect(self.start_countdown)
        group_btns_row2.addWidget(self.upload_sequence_btn)
        group_btns_row2.addWidget(self.start_btn)
        group_controls_v_layout.addLayout(group_btns_row2)
        
        # Style the Start button
        self.start_btn.setStyleSheet(f"font-size: 18px; padding: 10px; background-color: #E91E63;") 
        self.upload_sequence_btn.setEnabled(False) 

        # --- Row 3: Arm/Disarm Button ---
        self.arm_btn = QPushButton("ENABLE RAIL (Software Arm)")
        self.arm_btn.setCheckable(True)
        self.arm_btn.clicked.connect(self.toggle_arm_system)
        self.arm_btn.setStyleSheet("""
            QPushButton {
                font-size: 20px; font-weight: bold; padding: 12px;
                background-color: #455A64; /* Blue-grey for disarmed */
                color: white;
            }
            QPushButton:checked {
                background-color: #D32F2F; /* Red for armed */
            }
        """)
        group_controls_v_layout.addWidget(self.arm_btn)

        # Finally, add the completed vertical layout of controls to the right panel
        right_panel.addLayout(group_controls_v_layout)

        # --- Playground Area (Center) ---
        self.playground = QWidget()
        self.playground.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        playground_layout = QVBoxLayout(self.playground)
        playground_layout.setSpacing(10) 
        playground_layout.setContentsMargins(5, 5, 5, 5)
        self.software_toggle = QPushButton("With ESP32") 
        self.software_toggle.setCheckable(True)
        self.software_toggle.setChecked(self.software_only_mode) 
        self.software_toggle.clicked.connect(self.toggle_software)
        toggle_btn_container_layout = QHBoxLayout() 
        toggle_btn_container_layout.addStretch(1) 
        toggle_btn_container_layout.addWidget(self.software_toggle)
        playground_layout.addLayout(toggle_btn_container_layout)
        self.led_container = QWidget()
        self.led_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.grid = QGridLayout(self.led_container)
        self.grid.setSpacing(10) 
        playground_layout.addWidget(self.led_container, 1)

        # Main Layout Structure
        # All panels are added to the single, original layout
        layout.addLayout(left_panel, 25)
        layout.addWidget(self.playground, 50)
        layout.addLayout(right_panel, 25)
        self.update_button_states()
    
    # ... (rest of the MainWindow class)
    
    def handle_group_reorder(self, source_parent, source_start, source_end, dest_parent, dest_row):
        """Handles reordering of groups in the QListWidget and updates self.groups."""
        # This signal is complex. A simpler way is to rebuild self.groups based on QListWidget order
        # whenever an operation that could change order (like drag-drop) completes.
        # QListWidget's internal move already reorders its items. We need to sync self.groups.

        # Extract the moved item's widget's group name (assuming it's a GroupHeaderWidget)
        # This is tricky because the item might already be at its new position.
        # A more robust way is to rebuild self.groups from the QListWidget items' current order.
        self._save_state_for_undo()
        
        # Let's rebuild self.groups to match the new visual order
        new_ordered_groups_data = []
        temp_group_lookup = {g['name']: g for g in self.groups} # For quick lookup

        for i in range(self.group_list.count()):
            item = self.group_list.item(i)
            widget = self.group_list.itemWidget(item)
            if widget and isinstance(widget, GroupHeaderWidget):
                group_name = widget.group_name # Use stored group_name from widget
                if group_name in temp_group_lookup:
                    new_ordered_groups_data.append(temp_group_lookup[group_name])
                else:
                    print(f"Warning: Group '{group_name}' from list widget not found in internal data during reorder.")
            else:
                print(f"Warning: Item at row {i} is not a GroupHeaderWidget or widget is None.")


        if len(new_ordered_groups_data) == len(self.groups):
            self.groups = new_ordered_groups_data
            print("Internal groups reordered to match QListWidget.")
            self.generate_timeline() # Regenerate timeline based on new order
            self.calculate_time()
        else:
            print("Error: Mismatch in group count after reorder. Internal list not updated.")
            # Potentially force a full refresh or show an error.
            # This might happen if an item is being deleted while a drag is processed.

    # Method to get group data by name (useful for GroupHeaderWidget and other places)
    def get_group_by_name(self, name):
        for group in self.groups:
            if group["name"] == name:
                return group
        return None

     # In SECTION 8, inside MainWindow class

    def toggle_software(self):
        self.software_only_mode = self.software_toggle.isChecked()
        print(f"Mode changed to: {'Software Only' if self.software_only_mode else 'With ESP32'}")
        
        # --- NEW LOGIC ---
        # We no longer stop the connection here. We simply update the UI state.
        # The connection state (self.esp32_connected) remains unchanged.
        
        self.update_button_style()
        self.update_connection_status(self.esp32_connected) # Re-evaluate status display
        self.update_button_states()

        # If we are switching TO hardware mode and we are NOT connected,
        # then we should try to connect.
        if not self.software_only_mode and not self.esp32_connected:
            self.start_wireless_communication()

    def update_button_style(self):
        """Update the style of the software toggle button."""
        if self.software_only_mode:
            self.software_toggle.setStyleSheet("""
                QPushButton {
                    background-color: #4CAF50; /* Green for Software Only */
                    color: white;
                    border-radius: 10px; /* Consistent rounding */
                    padding: 8px;
                    font-size: 14px; /* Slightly smaller */
                }
            """)
            self.software_toggle.setText("Software Only")
        else:
            self.software_toggle.setStyleSheet("""
                QPushButton {
                    background-color: #9C27B0; /* Purple for With ESP32 */
                    color: white;
                    border-radius: 10px;
                    padding: 8px;
                    font-size: 14px;
                }
            """)
            self.software_toggle.setText("With ESP32")

# END SECTION 8: MainWindow Class - __init__ and initUI

# START SECTION 9: MainWindow Class - Serial Communication Methods
    def perform_auto_discovery(self):
        if self.software_only_mode:
            QMessageBox.information(self, "Info", "Auto Connect works only in Hardware Mode.")
            return

        print("Starting UDP Auto-Discovery...")
        self.connection_status_label.setText("Scanning Network...")
        QApplication.processEvents() 

        udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        udp_sock.settimeout(2.0) 
        
        try:
            # Broadcast
            print(f"Sending discovery packet to 255.255.255.255:{UDP_DISCOVERY_PORT}")
            udp_sock.sendto(DISCOVERY_PACKET, ('255.255.255.255', UDP_DISCOVERY_PORT))
            
            start_time = time.time()
            while time.time() - start_time < 2.5:
                try:
                    data, addr = udp_sock.recvfrom(1024)
                    msg = data.decode().strip()
                    if msg == DISCOVERY_REPLY:
                        print(f"Found ESP32 at {addr[0]}")
                        global ESP32_IP 
                        ESP32_IP = addr[0] # Update global IP
                        
                        self.connection_status_label.setText(f"Found at {ESP32_IP}")
                        QMessageBox.information(self, "Device Found", f"Pyro Controller found at {ESP32_IP}\nConnecting now...")
                        
                        self.start_wireless_communication()
                        return
                except socket.timeout:
                    break
            
            QMessageBox.warning(self, "Discovery Failed", "No controller found.\nCheck WiFi connection.")
            self.connection_status_label.setText("ESP32: Not Found")
            
        except Exception as e:
            print(f"UDP Error: {e}")
            QMessageBox.critical(self, "Error", f"Discovery Error: {e}")
        finally:
            udp_sock.close()

    def start_wireless_communication(self):
        print("Attempting to start wireless communication...")
        if self.software_only_mode:
            print("In Software Only Mode, communication is disabled.")
            self.update_connection_status(False)
            return

        if self.serial_worker is not None and self.serial_worker.isRunning():
            print("Stopping existing worker first...")
            self.serial_worker.stop()
            self.serial_worker = None

        try:
            # This is the main change: We now create a WiFiWorker
            # We can keep the variable name self.serial_worker for simplicity
            self.serial_worker = WiFiWorker(ESP32_IP)
            self.serial_worker.connection_status.connect(self.update_connection_status)
            self.serial_worker.data_received.connect(self.process_serial_data)
            self.serial_worker.start()
            print("WiFi worker thread started.")
        except Exception as e:
            print(f"Error starting wireless communication: {e}")
            self.update_connection_status(False)
            self.serial_worker = None

    def closeEvent(self, event):
        # We can keep ENABLE_WIFI check now
        if ENABLE_WIFI and self.serial_worker and self.serial_worker.isRunning():
            self.serial_worker.stop()
        event.accept()

    def update_connection_status(self, connected):
        self.esp32_connected = connected
        
        # --- NEW SIMPLIFIED LOGIC ---

        # Step 1: Handle the connection status label and color first.
        # This part is now independent of the software/hardware mode.
        if self.esp32_connected:
            # If we are in software mode, add the "(Standby)" text
            if self.software_only_mode:
                self.connection_status_label.setText("ESP32: Connected (Standby)")
            else:
                self.connection_status_label.setText("ESP32: Connected")
            # In either case, if we're connected, the color is GREEN.
            self.connection_status_label.setStyleSheet("font-size: 16px; color: #4CAF50;")
        else:
            # If we're not connected, it's always red.
            self.connection_status_label.setText("ESP32: Not Connected")
            self.connection_status_label.setStyleSheet("font-size: 16px; color: #FF6B6B;")

        # Step 2: Handle the sequence status label, which DOES depend on the mode.
        if self.software_only_mode:
            self.sequence_status_label.setText("Sequence: N/A (Software Mode)")
            self.sequence_status_label.setStyleSheet("font-size: 16px; color: #FFA500;")
        else:
            # We are in hardware mode. Display the actual sequence status.
            if self.esp32_connected:
                # On a new or restored connection, always reset the sequence status
                self.sequence_status_label.setText("Sequence: Not Uploaded")
                self.sequence_status_label.setStyleSheet("font-size: 16px; color: #FF6B6B;")
                self.sequence_uploaded = False
            else:
                self.sequence_status_label.setText("Sequence: Not Uploaded")
                self.sequence_status_label.setStyleSheet("font-size: 16px; color: #FF6B6B;")
                self.sequence_uploaded = False

        # Step 3: Update the enabled/disabled state of all buttons
        self.update_button_states()


    def process_serial_data(self, data):
        data = data.strip()
        if not data: return
        print(f"[ESP32 RX] {data}")

        # --- 1. RAIL SAFETY FEEDBACK ---
        if data.startswith("RAIL:"):
            status = data.split(":")[1].strip()
            if status == "LIVE":
                self.rail_status_label.setText("DANGER: RAIL LIVE (12V)")
                self.rail_status_label.setStyleSheet("font-size: 18px; font-weight: bold; color: white; background-color: #D32F2F; border: 2px solid #FF5252; padding: 5px; margin-top: 5px;")
            elif status == "SAFE":
                self.rail_status_label.setText("RAIL SAFE (No Power)")
                self.rail_status_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #81C784; border: 2px solid #4CAF50; padding: 5px; margin-top: 5px;")
            return

        # --- 2. SYNC ARM STATE FROM REMOTE ---
        if data == "ACK ARM":
            self.arm_btn.setChecked(True)
            self.arm_btn.setText("DISABLE RAIL (Armed)")
            self.arm_btn.setStyleSheet("background-color: #D32F2F; color: white; padding: 10px; font-weight: bold;")
            print("ESP32 confirmed: System is ARMED.")
            return
        elif data == "ACK DISARM":
            self.arm_btn.setChecked(False)
            self.arm_btn.setText("ENABLE RAIL (Disarmed)")
            self.arm_btn.setStyleSheet("background-color: #555; color: white; padding: 10px; font-weight: bold;")
            print("ESP32 confirmed: System is DISARMED.")
            return

        # --- 3. SYNC FIRING (MANUAL OR SEQUENCE) ---
        if data.startswith("FIRED_CUE:"):
            try:
                # Format: "FIRED_CUE:5"
                parts = data.split(":")
                if len(parts) > 1:
                    cue_number = int(parts[1].strip())
                    idx = cue_number - 1
                    
                    if 0 <= idx < len(self.leds):
                        # Flash the LED on the GUI
                        QTimer.singleShot(0, lambda i=idx: self.leds[i].activate())
                    else:
                        print(f"Warning: Received FIRED_CUE for out-of-range index: {cue_number}")
            except (ValueError, IndexError) as e:
                print(f"Error processing FIRED_CUE: '{data}' ({e})")
            return

        # --- 4. SEQUENCE UPLOAD LOGIC ---
        if data == "ACK_UPLOAD_JSON_START": 
            self.send_json_payload() 
            return
        elif data == "JSON_PARSE_OK":
            print("PROCESS_SERIAL_DATA: Received JSON_PARSE_OK")
            self.upload_confirmed_successful = True 

            if hasattr(self, 'upload_progress') and self.upload_progress:
                try:
                    self.upload_progress.canceled.disconnect(self.abort_upload)
                except TypeError: pass
                
                self.upload_progress.setValue(self.upload_progress.maximum())
                self.upload_progress.setLabelText("Upload successful! Sequence armed.")
                QTimer.singleShot(800, self.safe_close_progress_dialog) 
            
            self.sequence_uploaded = True
            if self.sequence_status_label : self.sequence_status_label.setText("Sequence: Uploaded & Ready")
            if self.sequence_status_label : self.sequence_status_label.setStyleSheet("font-size: 16px; color: #4CAF50;")
            
            QMessageBox.information(self, "Upload Success", "Sequence JSON uploaded and parsed by ESP32.")
            self.update_button_states()
            return
            
        elif data.startswith("JSON_PARSE_ERROR:"):
            if hasattr(self, 'upload_progress') and self.upload_progress:
                self.upload_progress.setLabelText(f"ESP32 Error: {data}")
            self.abort_upload(f"ESP32 JSON Parsing Error: {data.split(':', 1)[1]}")
            return
            
        # --- 5. EXECUTION STATUS ---
        if data == "ACK EXECUTE":
            print("ESP32 acknowledged EXECUTE_NOW command. Sequence running.")
        elif data == "ACK ABORT":
            print("ESP32 acknowledged ABORT_SEQUENCE command.")
            self.countdown_label.setText("Aborted by ESP32")
            self.sequence_uploaded = False
            if hasattr(self, 'countdown_update_timer') and self.countdown_update_timer.isActive():
                self.countdown_update_timer.stop()
            if hasattr(self, 'software_step_timer') and self.software_step_timer.isActive():
                self.software_step_timer.stop()
            self.update_button_states()
            
        elif data == "SEQUENCE_COMPLETE":
            print("ESP32 reported sequence complete.")
            if not self.software_only_mode:
                QMessageBox.information(self, "Sequence Info", "ESP32 reported: Sequence Complete!")
            self.countdown_label.setText("Finished")
            if hasattr(self, 'countdown_update_timer') and self.countdown_update_timer.isActive(): self.countdown_update_timer.stop()
            if hasattr(self, 'software_step_timer') and self.software_step_timer.isActive(): self.software_step_timer.stop()
            self.sequence_uploaded = False
            self.sequence_status_label.setText("Sequence: Not Uploaded")
            self.sequence_status_label.setStyleSheet("font-size: 20px; color: #FF0000;")
            self.update_button_states()
            
        elif data.startswith("ERROR:") or data.startswith("NACK:"):
            error_message = f"Received error/NACK from ESP32: {data}"
            print(error_message)
            is_uploading = hasattr(self, 'upload_progress') and self.upload_progress and self.upload_progress.isVisible()
            if is_uploading:
                self.abort_upload(error_message)
            else:
                QMessageBox.critical(self, "ESP32 Error", error_message)
                self.countdown_label.setText("Error!")
                self.update_button_states()
                
        elif data.startswith("STATUS:"):
             print(f"ESP32 Status Update: {data}")

       

        # Handle ACKs for JSON Upload
        
        if data == "ACK_UPLOAD_JSON_START": # ESP32 is ready for the JSON payload
            self.send_json_payload() # Proceed to send the actual JSON
            return
        elif data == "JSON_PARSE_OK":
            print("PROCESS_SERIAL_DATA: Received JSON_PARSE_OK")
            self.upload_confirmed_successful = True # Set a flag just in case

            if hasattr(self, 'upload_progress') and self.upload_progress:
                print("PROCESS_SERIAL_DATA: Progress dialog exists.")
                try:
                    self.upload_progress.canceled.disconnect(self.abort_upload)
                    print("PROCESS_SERIAL_DATA: Disconnected QProgressDialog.canceled from abort_upload.")
                except TypeError:
                    print("PROCESS_SERIAL_DATA: QProgressDialog.canceled was not connected or already disconnected.")
                
                self.upload_progress.setValue(self.upload_progress.maximum())
                self.upload_progress.setLabelText("Upload successful! Sequence armed.")
                # Option: Close it after a short delay to show the success message in it
                QTimer.singleShot(800, self.safe_close_progress_dialog) # Use a helper to close
            
            self.sequence_uploaded = True
            if self.sequence_status_label : self.sequence_status_label.setText("Sequence: Uploaded & Ready")
            if self.sequence_status_label : self.sequence_status_label.setStyleSheet("font-size: 16px; color: #4CAF50;")
            
            # Show the success message *after* handling the progress dialog
            QMessageBox.information(self, "Upload Success", "Sequence JSON uploaded and parsed by ESP32.")
            
            self.update_button_states()
            return
        elif data.startswith("JSON_PARSE_ERROR:"):
            if hasattr(self, 'upload_progress') and self.upload_progress:
                self.upload_progress.setLabelText(f"ESP32 Error: {data}")
                # self.upload_progress.cancel() # This would call abort_upload again
            self.abort_upload(f"ESP32 JSON Parsing Error: {data.split(':', 1)[1]}")
            return
        elif data == "ACK_CHECKSUM_OK": # If ESP32 verifies checksum of JSON
            # This would be part of a multi-stage ACK for JSON upload
            print("ESP32 confirmed JSON checksum.")
            # Potentially wait for final "ARMED" signal after this
            return


        # Existing feedback handlers (largely unchanged)
        if data == "ACK EXECUTE":
            print("ESP32 acknowledged EXECUTE_NOW command. Sequence running.")
        elif data == "ACK ABORT":
            print("ESP32 acknowledged ABORT_SEQUENCE command.")
            self.countdown_label.setText("Aborted by ESP32")
            self.sequence_uploaded = False
            if hasattr(self, 'countdown_update_timer') and self.countdown_update_timer.isActive():
                self.countdown_update_timer.stop()
            if hasattr(self, 'software_step_timer') and self.software_step_timer.isActive():
                self.software_step_timer.stop()
            self.update_button_states()
        elif data.startswith("FIRED_CUE:"):
            try:
                # Format: "FIRED_CUE:5"
                # Split by colon, take second part, strip whitespace
                parts = data.split(":")
                if len(parts) > 1:
                    cue_number = int(parts[1].strip())
                    idx = cue_number - 1
                    
                    # Safety check: Ensure index is within bounds of our LED list
                    if 0 <= idx < len(self.leds):
                        # Use QTimer.singleShot(0, ...) to ensure UI update happens on main thread
                        # This is critical if data_received comes from a worker thread
                        QTimer.singleShot(0, lambda i=idx: self.leds[i].activate())
                    else:
                        print(f"Warning: Received FIRED_CUE for out-of-range index: {cue_number}")
            except (ValueError, IndexError) as e:
                print(f"Error processing FIRED_CUE: '{data}' ({e})")
        elif data == "SEQUENCE_COMPLETE":
            print("ESP32 reported sequence complete.")
            if not self.software_only_mode:
                QMessageBox.information(self, "Sequence Info", "ESP32 reported: Sequence Complete!")
            self.countdown_label.setText("Finished")
            if hasattr(self, 'countdown_update_timer') and self.countdown_update_timer.isActive(): self.countdown_update_timer.stop()
            if hasattr(self, 'software_step_timer') and self.software_step_timer.isActive(): self.software_step_timer.stop()
            self.sequence_uploaded = False
            self.sequence_status_label.setText("Sequence: Not Uploaded")
            self.sequence_status_label.setStyleSheet("font-size: 20px; color: #FF0000;")
            self.update_button_states()
        elif data.startswith("ERROR:") or data.startswith("NACK:"):
            error_message = f"Received error/NACK from ESP32: {data}"
            print(error_message)
            # If uploading, abort the process
            is_uploading = hasattr(self, 'upload_progress') and self.upload_progress and self.upload_progress.isVisible()
            if is_uploading:
                self.abort_upload(error_message)
            else:
                QMessageBox.critical(self, "ESP32 Error", error_message)
                self.countdown_label.setText("Error!")
                if hasattr(self, 'countdown_update_timer') and self.countdown_update_timer.isActive(): self.countdown_update_timer.stop()
                if hasattr(self, 'software_step_timer') and self.software_step_timer.isActive(): self.software_step_timer.stop()
                self.sequence_uploaded = False
                self.update_button_states()
        elif data.startswith("STATUS:"):
             print(f"ESP32 Status Update: {data}")

        if data == "ACK ARM":
         print("ESP32 confirmed: System is ARMED.")
         return # Acknowledge and do nothing further
        if data == "ACK DISARM":
         print("ESP32 confirmed: System is DISARMED.")
         return # Acknowledge and do nothing further     

    def safe_close_progress_dialog(self):
        if hasattr(self, 'upload_progress') and self.upload_progress:
            if self.upload_progress.isVisible():
                self.upload_progress.close()
            self.upload_progress = None # Clear the reference
            print("SAFE_CLOSE_PROGRESS: Progress dialog closed and reference cleared.")         

# END SECTION 9: MainWindow Class - Serial Communication Methods

# START SECTION 10: MainWindow Class - Toggle and Cracker Methods
    # update_button_style is now in initUI section
    # toggle_software is now in initUI section

    def init_crackers(self):
        num_text = self.num_input.text()
        if not num_text.isdigit():
            QMessageBox.critical(self, "Error", "Please enter a valid number!")
            return

        num = int(num_text)
        if num <= 0:
            QMessageBox.critical(self, "Error", "Number must be greater than 0!")
            return

        # *** LOGIC CHANGE 1: Call the GUI-only reset function ***
        # This will clear the interface without sending any commands to the ESP32.
        self._reset_gui() 
        
        self._add_crackers_internal(num)

    def add_crackers_button_clicked(self, count):
        """Handler for 'Add 1' and 'Add 5' buttons."""
        self._add_crackers_internal(count)

    def _add_crackers_internal(self, count_to_add):
        current_led_count = len(self.leds)
        cols = min(10, max(5, int(len(self.leds)**0.5) + 1 if len(self.leds) > 0 else 5) )

        for i in range(count_to_add):
            led_number = current_led_count + i + 1
            led = DraggableLED(
                text=str(led_number),
                parent=self.led_container,
                main_window=self
            )
            total_leds_so_far = current_led_count + i
            row = total_leds_so_far // cols
            col = total_leds_so_far % cols
            self.grid.addWidget(led, row, col)
            self.leds.append(led)

        self.update_stats()
        self.calculate_time()

    def _reset_gui(self):
        """
        NEW FUNCTION: Resets only the GUI elements to a clean state.
        This function does NOT communicate with the ESP32.
        """
        # Clear LEDs from the grid and the list
        for led_widget in self.leds:
            if led_widget:
                self.grid.removeWidget(led_widget)
                led_widget.deleteLater()
        self.leds.clear()

        # Clear groups from the list widget and internal list
        self.group_list.clear()
        self.groups.clear()

        self.timeline_list.clear()
        self.num_input.clear()

        self.update_stats()
        self.time_label.setText("Estimated Time: 0s")
        self.countdown_label.setText("")
        self.current_highlight = None
        self.sequence_uploaded = False
        if not self.software_only_mode:
            self.sequence_status_label.setText("Sequence: Not Uploaded")
            self.sequence_status_label.setStyleSheet("font-size: 20px; color: #FF0000;")
        
        self.update_button_states()
        print("GUI has been reset.")

    def reset_program(self):
        """
        MODIFIED FUNCTION: This is now a full system reset. It resets the GUI
        AND sends an abort command to the ESP32 if connected.
        """
        # Step 1: Reset the local GUI
        self._reset_gui()
        
        # Step 2: Send the hardware abort command
        if self.serial_worker and self.esp32_connected:
            print("Sending ABORT_SEQUENCE to ESP32...")
            self.serial_worker.send_data("ABORT_SEQUENCE" + '\n')

        print("Full program reset initiated.")
# END SECTION 10: MainWindow Class - Toggle and Cracker Methods

# START SECTION 11: MainWindow Class - Stats, Time, and Group Management Methods
    def update_stats(self):
        num_actual_groups = sum(1 for g in self.groups if g.get("pattern") != "Pause")
        num_pause_groups = len(self.groups) - num_actual_groups
        self.stats_label.setText(f"Crackers: {len(self.leds)} | Groups: {num_actual_groups} (+{num_pause_groups} Pauses)")

    def calculate_time(self):
        total_ms = 0
        ordered_group_data_list = [] # To store groups in their display order
        
        # Ensure group_list exists
        if not hasattr(self, 'group_list'): 
            if self.time_label: self.time_label.setText("Est. Time: Error")
            return

        for i in range(self.group_list.count()):
            item = self.group_list.item(i)
            widget = self.group_list.itemWidget(item)
            if widget and isinstance(widget, GroupHeaderWidget):
                group_data = self.get_group_by_name(widget.group_name)
                if group_data: ordered_group_data_list.append(group_data)

        for group in ordered_group_data_list:
            pattern = group.get("pattern")
            gap = group.get("gap", 0) # Delay BETWEEN firing events in this group

            if pattern == "Pause":
                total_ms += gap 
            elif pattern and pattern != "None":
                zero_based_members = sorted(list(set(group.get("members", []))))
                if not zero_based_members: continue # Skip if no members for a firing pattern
                
                # get_sequence_order now returns a list of firing events (each event is a list of cues)
                firing_events_0_based = self.get_sequence_order(zero_based_members, pattern)
                num_firing_events = len(firing_events_0_based)
                
                if num_firing_events > 0:
                    # Each firing event takes 'gap' milliseconds until the next event or end of group
                    total_ms += num_firing_events * gap
        
        if self.time_label: self.time_label.setText(f"Est. Time: {total_ms / 1000:.1f}s")


    def add_group(self):
        self._save_state_for_undo()
        dialog = CustomInputDialog(self, title="Create Firing Group", label="Enter group name:")
        
        # Check if the user clicked "OK"
        if dialog.exec_() == QDialog.Accepted:
            group_name = dialog.get_text().strip()

            # --- All the logic that uses group_name is now safely inside this block ---

            # If the user clicked OK but left the name blank, do nothing.
            if not group_name:
                return

            # Check for duplicate names
            if any(g["name"].lower() == group_name.lower() for g in self.groups):
                QMessageBox.warning(self, "Duplicate Name", "A group or pause with this name already exists!")
                return
            
            # Create and add the new group
            hue = (len([g for g in self.groups if g.get("pattern") != "Pause"]) * 60 + 120) % 360
            group_color = QColor().fromHsv(hue, 255, 200)
            new_group_data = {
                "name": group_name, "members": [], "pattern": None,
                "color": group_color, "gap": 500, "is_pause_group": False
            }
            self._add_group_to_gui_and_data(new_group_data)
            
            # Open the member editor for the new group
            self.edit_group_members(group_name)
            
            # Update UI
            self.update_stats()
            self.generate_timeline()
            self.calculate_time()
        
        # If the user clicked Cancel or 'X', the code inside the 'if' block is
        # skipped entirely, and the function simply ends, preventing the crash.


    def add_pause_group(self):
        self._save_state_for_undo() # <<< ADD THIS LINE
        duration_ms, ok = QInputDialog.getInt(self, "Add Pause", "Enter pause duration (milliseconds):", 1000, 100, 600000, 100)
        if not ok:
            return

        # Auto-generate a unique name for the pause
        pause_count = sum(1 for g in self.groups if g.get("pattern") == "Pause") + 1
        pause_name = f"Pause_{duration_ms}ms_#{pause_count}"
        while any(g["name"].lower() == pause_name.lower() for g in self.groups):
            pause_count +=1
            pause_name = f"Pause_{duration_ms}ms_#{pause_count}"


        new_pause_data = {
            "name": pause_name,
            "members": [], # Pause groups have no members
            "pattern": "Pause", # Special pattern identifier
            "color": QColor(80, 80, 80), # Distinct color for pause groups in list
            "gap": duration_ms, # The 'gap' for a pause group is its duration
            "is_pause_group": True
        }
        self._add_group_to_gui_and_data(new_pause_data)
        self.update_stats()
        self.generate_timeline()
        self.calculate_time()

    def _add_group_to_gui_and_data(self, group_data, append_to_data=True):
        """
        Helper to add group to QListWidget. If append_to_data is True, it also
        adds the group to the self.groups list (for new groups). When loading
        from a file, we set this to False to prevent duplicates.
        """
        if append_to_data:
            self.groups.append(group_data)

        item = QListWidgetItem(self.group_list)
        widget = GroupHeaderWidget(group_data, self)
        item.setSizeHint(widget.sizeHint())
        self.group_list.addItem(item)
        self.group_list.setItemWidget(item, widget)

        # Ensure self.groups stays in sync with QListWidget's visual order
        # This is now handled by handle_group_reorder after drag/drop.
        # For additions, they are appended to both, so order is consistent initially.
# END SECTION 11: MainWindow Class - Stats, Time, and Group Management Methods

# START SECTION 12: MainWindow Class - Group Editing and Deletion Methods
    def edit_group_members(self, group_name):
        self._save_state_for_undo()
        group = self.get_group_by_name(group_name)
        if not group or group.get("pattern") == "Pause": # Should not be called for pause groups
            QMessageBox.critical(self, "Error", "Group not found or cannot edit members of a pause group!")
            return

        dialog = MemberEditDialog(len(self.leds), group["members"], self)
        if dialog.exec_() == QDialog.Accepted:
            # Remove old members' group association and color
            for idx in group["members"]:
                if 0 <= idx < len(self.leds):
                    self.leds[idx].group = None
                    self.leds[idx].set_color(QColor(100, 100, 100)) # Reset to default

            new_members = dialog.get_selected()
            group["members"] = new_members
            # Assign new members' group association and color
            for idx in new_members:
                if 0 <= idx < len(self.leds):
                    self.leds[idx].group = group_name
                    self.leds[idx].set_color(group["color"])

            self.generate_timeline()
            self.calculate_time()
            self.update_button_states() # In case group becomes empty/valid

    def edit_pause_duration(self, pause_name):
        self._save_state_for_undo() # <<< ADD THIS LINE
        pause_group = self.get_group_by_name(pause_name)
        if not pause_group or pause_group.get("pattern") != "Pause":
            QMessageBox.critical(self, "Error", "Pause entry not found!")
            return

        current_duration = pause_group.get("gap", 1000)
        new_duration, ok = QInputDialog.getInt(self, "Edit Pause Duration",
                                               "Enter new duration (milliseconds):",
                                               current_duration, 100, 600000, 100)
        if ok and new_duration != current_duration:
            pause_group["gap"] = new_duration
            # Update the name if it includes duration, or just the display in GroupHeaderWidget
            # For simplicity, let's assume name might not auto-update here, but display will.
            # Find the widget in group_list and update its display
            for i in range(self.group_list.count()):
                item = self.group_list.item(i)
                widget = self.group_list.itemWidget(item)
                if widget and isinstance(widget, GroupHeaderWidget) and widget.group_name == pause_name:
                    widget.update_pattern_display(pause_group) # Update display
                    break
            self.generate_timeline()
            self.calculate_time()


    def delete_group(self, group_name_to_delete):
        self._save_state_for_undo() # <<< ADD THIS LINE

        # Confirm deletion
        confirm = QMessageBox.question(self, "Delete Entry",
                                       f"Are you sure you want to delete '{group_name_to_delete}'?",
                                       QMessageBox.Yes | QMessageBox.No)
        if confirm == QMessageBox.No:
            return

        group_to_delete = self.get_group_by_name(group_name_to_delete)
        if not group_to_delete:
            QMessageBox.warning(self, "Error", f"'{group_name_to_delete}' not found for deletion.")
            return

        # If it's a firing group, unassign its members
        if group_to_delete.get("pattern") != "Pause":
            for idx in group_to_delete.get("members", []):
                if 0 <= idx < len(self.leds):
                    self.leds[idx].group = None
                    self.leds[idx].set_color(QColor(100, 100, 100)) # Reset to default

        # Remove from QListWidget
        for i in range(self.group_list.count()):
            item = self.group_list.item(i)
            widget = self.group_list.itemWidget(item)
            if widget and isinstance(widget, GroupHeaderWidget) and widget.group_name == group_name_to_delete:
                self.group_list.takeItem(i) # Removes item and associated widget
                break
        
        # Remove from self.groups data
        self.groups = [g for g in self.groups if g["name"] != group_name_to_delete]

        self.update_stats()
        self.generate_timeline()
        self.calculate_time()
        self.update_button_states()
# END SECTION 12: MainWindow Class - Group Editing and Deletion Methods

# START SECTION 13: MainWindow Class - Pattern, Rename, and Countdown Methods
    def select_pattern(self, group_name):
        self._save_state_for_undo()
        group = self.get_group_by_name(group_name)
        if not group or group.get("pattern") == "Pause": # Should not be called for pause groups
            QMessageBox.critical(self, "Error", "Group not found or cannot select pattern for a pause group!")
            return

        dialog = PatternDialog(self.patterns, self) # self.patterns is for regular groups
        dialog.pattern_combo.setCurrentText(group.get("pattern") if group.get("pattern") else "None")
        dialog.gap_spin.setValue(group.get("gap", 500)) # Set current gap

        if dialog.exec_() == QDialog.Accepted:
            new_pattern = dialog.pattern_combo.currentText()
            group["pattern"] = new_pattern if new_pattern != "None" else None
            group["gap"] = dialog.gap_spin.value()

            # Update GroupHeaderWidget display
            for i in range(self.group_list.count()):
                item = self.group_list.item(i)
                widget = self.group_list.itemWidget(item)
                if widget and isinstance(widget, GroupHeaderWidget) and widget.group_name == group_name:
                    widget.update_pattern_display(group)
                    break
            self.generate_timeline()
            self.calculate_time()

    def rename_group(self, old_name):
        self._save_state_for_undo()
        group = self.get_group_by_name(old_name)
        if not group:
            QMessageBox.critical(self, "Error", "Entry not found!")
            return

        title = "Rename Pause" if group.get("pattern") == "Pause" else "Rename Group"
        
        dialog = CustomInputDialog(self, title=title, label="New name:", initial_text=old_name)

        # Check if the user clicked "OK"
        if dialog.exec_() == QDialog.Accepted:
            new_name = dialog.get_text().strip()
            
            # --- All the logic that uses new_name is now safely inside this block ---

            # If the name is blank or unchanged, do nothing.
            if not new_name or new_name == old_name:
                return

            # Check for duplicates
            if any(g["name"].lower() == new_name.lower() for g in self.groups if g["name"] != old_name):
                QMessageBox.warning(self, "Duplicate Name", "This name is already in use!")
                return

            # Update the data
            group["name"] = new_name
            if group.get("pattern") != "Pause":
                for idx in group.get("members", []):
                    if 0 <= idx < len(self.leds):
                        self.leds[idx].group = new_name
            
            # Update the UI widget
            for i in range(self.group_list.count()):
                item = self.group_list.item(i)
                widget = self.group_list.itemWidget(item)
                if isinstance(widget, GroupHeaderWidget) and widget.group_name == old_name:
                    widget.group_name = new_name
                    widget.name_label.setText(new_name)
                    break
            
            self.generate_timeline()

    def start_countdown(self):
        if not self.groups:
            QMessageBox.critical(self, "Error", "Cannot start: No groups or pauses defined.")
            return
        
        # Check if there's at least one runnable entry (not just empty pauses or unconfigured groups)
        is_runnable = any(g.get("pattern") and (g.get("pattern") != "None" or g.get("pattern") == "Pause") for g in self.groups)
        if not is_runnable:
            QMessageBox.critical(self, "Error", "Cannot start: No runnable groups or pauses in the sequence.")
            return

        if self.software_only_mode:
            print("Starting sequence in Software Only mode.")
        else:
            if not self.esp32_connected:
                QMessageBox.critical(self, "Error", "Cannot start: ESP32 not connected!")
                return
            if not self.sequence_uploaded or self.sequence_status_label.text() != "Sequence: Uploaded & Ready":
                QMessageBox.critical(self, "Error", "Cannot start: Sequence not uploaded or not ready on ESP32!")
                return
            print("Starting sequence in ESP32 Hardware mode.")

        print("Start button pressed. Initiating countdown...")
        self.countdown_val = 5 # Store countdown value
        self.countdown_label.setText(f"Starting in: {self.countdown_val}s")
        self.update_button_states(is_counting_down_or_running=True)

        if hasattr(self, 'countdown_update_timer') and self.countdown_update_timer.isActive():
            self.countdown_update_timer.stop()
        self.countdown_update_timer = QTimer(self)
        self.countdown_update_timer.timeout.connect(self.update_countdown_display)
        self.countdown_update_timer.start(1000)

        QTimer.singleShot(5000, self.trigger_sequence_execution) # Renamed for clarity

    def update_countdown_display(self):
        self.countdown_val -= 1
        if self.countdown_val > 0:
            self.countdown_label.setText(f"Starting in: {self.countdown_val}s")
        elif self.countdown_val == 0:
            self.countdown_update_timer.stop()
            # Label changed by trigger_sequence_execution

    def trigger_sequence_execution(self): # Renamed from start_gui_sequence_visuals
        print("Countdown finished. Triggering sequence execution.")
        if hasattr(self, 'countdown_update_timer') and self.countdown_update_timer.isActive():
            self.countdown_update_timer.stop()
        self.countdown_label.setText("Running...")

        if self.software_only_mode:
            print("Executing sequence in software-only mode.")
            if hasattr(self, 'software_step_timer') and self.software_step_timer.isActive():
                self.software_step_timer.stop()
            self.execute_software_sequence()
        else:
            print("Hardware mode: Sending EXECUTE_NOW command to ESP32.")
            if self.serial_worker and self.esp32_connected:
                # *** CHANGE IS HERE: Add newline manually ***
                if not self.serial_worker.send_data("EXECUTE_NOW" + '\n'):
                    QMessageBox.critical(self, "Serial Error", "Failed to send EXECUTE_NOW command!")
                    self.countdown_label.setText("Error!")
                    self.update_button_states()
                    return
            else:
                QMessageBox.critical(self, "Error", "Cannot start hardware sequence: ESP32 not connected or worker missing.")
                self.countdown_label.setText("Error!")
                self.update_button_states()
                return
            print("Hardware mode: Waiting for FIRED_CUE messages for visuals.")

    def toggle_arm_system(self):
        if self.software_only_mode:
            self.arm_btn.setChecked(False)
            QMessageBox.information(self, "Info", "Arm/Disarm is a hardware-only feature.")
            return

        if not self.esp32_connected:
            self.arm_btn.setChecked(False)
            QMessageBox.warning(self, "Error", "Cannot arm system, ESP32 not connected.")
            return
        
        if self.arm_btn.isChecked():
            reply = QMessageBox.question(self, "Safety Warning", 
                "Enable firing rail MOSFET?\nEnsure physical key is OFF for test mode.", 
                QMessageBox.Yes | QMessageBox.No)
            
            if reply == QMessageBox.Yes:
                self.serial_worker.send_data("ARM_SYSTEM\n")
                self.arm_btn.setText("DISABLE RAIL (Armed)")
                self.arm_btn.setStyleSheet("background-color: #D32F2F; color: white; padding: 10px; font-weight: bold;")
            else:
                self.arm_btn.setChecked(False)
        else:
            self.serial_worker.send_data("DISARM_SYSTEM\n")
            self.arm_btn.setText("ENABLE RAIL (Disarmed)")
            self.arm_btn.setStyleSheet("background-color: #555; color: white; padding: 10px; font-weight: bold;")

# END SECTION 13: MainWindow Class - Pattern, Rename, and Countdown Methods

# START SECTION 14: MainWindow Class - Timeline and Sequence Methods
    def generate_timeline(self):
        if not hasattr(self, 'timeline_list'): return
        self.timeline_list.clear()
        time_offset_ms = 0

        # Use the visual order from self.group_list
        # Ensure group_list exists (it should be created in initUI)
        if not hasattr(self, 'group_list'): 
            print("generate_timeline: group_list not found")
            return

        for i in range(self.group_list.count()):
            item = self.group_list.item(i)
            widget = self.group_list.itemWidget(item)
            if not (widget and isinstance(widget, GroupHeaderWidget)): continue
            
            group = self.get_group_by_name(widget.group_name)
            if not group: continue

            group_name_display = group["name"]
            pattern = group.get("pattern")
            gap = group.get("gap", 0) # This is delay BETWEEN firing events in this group
            
            if pattern == "Pause":
                timeline_item = QListWidgetItem(f"{time_offset_ms / 1000:.1f}s - {group_name_display} (Pause: {gap}ms)")
                self.timeline_list.addItem(timeline_item)
                time_offset_ms += gap # Duration of pause is its gap
            elif pattern and pattern != "None":
                zero_based_members = sorted(list(set(group.get("members", []))))
                if not zero_based_members: # Skip if group has a pattern but no members
                    # Optional: Add a timeline entry indicating an empty group if desired
                    # timeline_item = QListWidgetItem(f"{time_offset_ms / 1000:.1f}s - {group_name_display} (Pattern: {pattern}, No members)")
                    # self.timeline_list.addItem(timeline_item)
                    continue

                firing_events_0_based = self.get_sequence_order(zero_based_members, pattern)
                if not firing_events_0_based: # Skip if pattern results in no events
                    # Optional: Add a timeline entry indicating an un-runnable pattern
                    # timeline_item = QListWidgetItem(f"{time_offset_ms / 1000:.1f}s - {group_name_display} (Pattern: {pattern}, No events)")
                    # self.timeline_list.addItem(timeline_item)
                    continue

                for event_cues_0_based in firing_events_0_based:
                    if not event_cues_0_based: continue # Should not happen if get_sequence_order is correct

                    # Convert 0-based indices to 1-based strings for display, sorted for readability
                    cues_to_fire_str = ", ".join(map(lambda x: str(x + 1), sorted(event_cues_0_based)))
                    
                    event_description = f"Fire ({cues_to_fire_str})"
                    if pattern == "All Fire" and len(event_cues_0_based) == len(zero_based_members):
                        event_description = f"All Fire ({cues_to_fire_str})"
                    
                    timeline_text = f"{time_offset_ms / 1000:.1f}s - {group_name_display}: {event_description}"
                    timeline_item = QListWidgetItem(timeline_text)
                    self.timeline_list.addItem(timeline_item)
                    time_offset_ms += gap # Each firing event is followed by the group's gap

    def get_sequence_order(self, members, pattern_name):
        """
        Returns a list of "firing events". Each event is a list of 0-based cue indices
        that should be fired simultaneously for that event.
        Assumes 'members' is a sorted list of 0-based cue indices.
        """
        if not members:
            return []

        sequence_events = []

        if pattern_name == "Right Wave":
            for cue_idx in members:
                sequence_events.append([cue_idx]) # Each cue is its own event
            return sequence_events

        elif pattern_name == "Left Wave":
            for cue_idx in reversed(members):
                sequence_events.append([cue_idx]) # Each cue is its own event
            return sequence_events

        elif pattern_name == "Alternate":
            # Your definition: 0, then N-1, then 1, then N-2, etc. (single fires)
            # Example [0, 1, 2, 3, 4] -> Fires 0, then 4, then 1, then 3, then 2
            # Sequence of events: [[0], [4], [1], [3], [2]]
            temp_members = list(members) # Make a copy to pop from
            while temp_members:
                sequence_events.append([temp_members.pop(0)]) # Fire from left
                if temp_members:
                    sequence_events.append([temp_members.pop(-1)]) # Fire from right
            return sequence_events
            
        elif pattern_name == "Center Burst":
            # Your definition:
            # Odd [0,1,2,3,4] (mid 2): Event1=[2], Event2=[1,3], Event3=[0,4]
            # Even [0,1,2,3] (mids 1,2): Event1=[1,2], Event2=[0,3]
            n = len(members)
            if n == 0: return []

            if n % 2 == 1: # Odd length
                mid_point_idx_in_members = n // 2
                sequence_events.append([members[mid_point_idx_in_members]]) # Center fires first
                for i in range(1, (n // 2) + 1):
                    simultaneous_pair = []
                    if mid_point_idx_in_members - i >= 0:
                        simultaneous_pair.append(members[mid_point_idx_in_members - i])
                    if mid_point_idx_in_members + i < n:
                        simultaneous_pair.append(members[mid_point_idx_in_members + i])
                    if simultaneous_pair: # Only add if pair is not empty
                        sequence_events.append(sorted(simultaneous_pair)) # Sort for consistent output
            else: # Even length
                mid_right_idx_in_members = n // 2
                mid_left_idx_in_members = mid_right_idx_in_members - 1
                
                initial_pair = []
                if mid_left_idx_in_members >=0: initial_pair.append(members[mid_left_idx_in_members])
                if mid_right_idx_in_members < n: initial_pair.append(members[mid_right_idx_in_members])
                if initial_pair: sequence_events.append(sorted(initial_pair))

                for i in range(1, n // 2): # Iterate to form subsequent pairs
                    simultaneous_pair = []
                    left_target_idx = mid_left_idx_in_members - i
                    right_target_idx = mid_right_idx_in_members + i
                    
                    # Ensure indices are valid before accessing members
                    if left_target_idx >= 0 :
                        simultaneous_pair.append(members[left_target_idx])
                    if right_target_idx < n:
                        simultaneous_pair.append(members[right_target_idx])
                    
                    if simultaneous_pair: # Only add if pair is not empty
                        sequence_events.append(sorted(simultaneous_pair))
            return sequence_events

        elif pattern_name == "Inwards":
            # Your definition:
            # [0,1,2,3,4] -> Event1=[0,4], Event2=[1,3], Event3=[2]
            # [0,1,2,3,4,5] -> Event1=[0,5], Event2=[1,4], Event3=[2,3]
            left_ptr = 0
            right_ptr = len(members) - 1
            while left_ptr <= right_ptr:
                current_event = []
                if left_ptr == right_ptr: # Middle element for odd length
                    current_event.append(members[left_ptr])
                else:
                    current_event.append(members[left_ptr])
                    current_event.append(members[right_ptr])
                sequence_events.append(sorted(current_event)) # Sort for consistent output
                left_ptr += 1
                right_ptr -= 1
            return sequence_events

        elif pattern_name == "All Fire":
            if members: 
                sequence_events.append(sorted(list(members))) # Single event with all members, sorted
            return sequence_events
            
        return [] # Default for "None" pattern or unknown


    def execute_software_sequence(self):
        print("Starting non-blocking software-only sequence execution.")
        if not self.groups:
            print("No groups to execute in software.")
            if self.countdown_label: self.countdown_label.setText("Finished")
            self.update_button_states()
            return

        self.software_sequence_group_index = 0
        self.software_sequence_event_index = 0 # Tracks event index within a group
        
        self.ordered_groups_for_sw_execution = []
        if hasattr(self, 'group_list'):
            for i in range(self.group_list.count()):
                item = self.group_list.item(i)
                widget = self.group_list.itemWidget(item)
                if widget and isinstance(widget, GroupHeaderWidget):
                    group_data = self.get_group_by_name(widget.group_name)
                    if group_data:
                        self.ordered_groups_for_sw_execution.append(group_data)
        
        if not self.ordered_groups_for_sw_execution:
            print("No valid groups found in list widget for software execution.")
            if self.countdown_label: self.countdown_label.setText("Finished")
            self.update_button_states()
            return

        # Initialize current_group_firing_events for the first group
        if self.ordered_groups_for_sw_execution:
            first_group = self.ordered_groups_for_sw_execution[0]
            pattern_name = first_group.get("pattern", "None")
            if pattern_name != "Pause" and pattern_name != "None":
                 members = sorted(list(set(first_group.get("members", []))))
                 self.current_group_firing_events = self.get_sequence_order(members, pattern_name)
            else: # Pause group or None pattern group
                self.current_group_firing_events = [] # No firing events, just a gap for Pause
        else:
            self.current_group_firing_events = []


        if not hasattr(self, 'software_step_timer') or self.software_step_timer is None:
            self.software_step_timer = QTimer(self)
            self.software_step_timer.setSingleShot(True)
            self.software_step_timer.timeout.connect(self.run_next_software_step) # Name matches
        elif self.software_step_timer.isActive():
            self.software_step_timer.stop()
        
        self.software_step_timer.start(10) # Start with a small delay

    def run_next_software_step(self):
        if self.software_sequence_group_index >= len(self.ordered_groups_for_sw_execution):
            print("Software sequence finished.")
            if self.countdown_label: self.countdown_label.setText("Finished")
            if hasattr(self, 'software_step_timer') and self.software_step_timer and self.software_step_timer.isActive():
                self.software_step_timer.stop()
            self.update_button_states()
            return

        current_group = self.ordered_groups_for_sw_execution[self.software_sequence_group_index]
        group_name = current_group["name"]
        pattern = current_group.get("pattern")
        gap_ms = current_group.get("gap", 500)

        if pattern == "Pause":
            print(f"Software pause: Group '{group_name}' for {gap_ms}ms")
            QApplication.processEvents()
            self.software_sequence_group_index += 1
            self.software_sequence_event_index = 0 
            
            if self.software_sequence_group_index < len(self.ordered_groups_for_sw_execution):
                next_group = self.ordered_groups_for_sw_execution[self.software_sequence_group_index]
                pattern_name = next_group.get("pattern", "None")
                if pattern_name != "Pause" and pattern_name != "None":
                    members = sorted(list(set(next_group.get("members", []))))
                    self.current_group_firing_events = self.get_sequence_order(members, pattern_name)
                else: self.current_group_firing_events = []
            else:
                self.current_group_firing_events = []

            if self.software_step_timer: self.software_step_timer.start(max(1, gap_ms))
            return

        # Handle cases where a non-Pause group might have no members or an invalid pattern
        # self.current_group_firing_events should have been populated when moving to this group
        if not hasattr(self, 'current_group_firing_events') or not self.current_group_firing_events:
            print(f"Skipping group '{group_name}' (no firing events generated or no members).")
            self.software_sequence_group_index += 1
            self.software_sequence_event_index = 0
            if self.software_sequence_group_index < len(self.ordered_groups_for_sw_execution):
                next_group = self.ordered_groups_for_sw_execution[self.software_sequence_group_index]
                pattern_name = next_group.get("pattern", "None")
                if pattern_name != "Pause" and pattern_name != "None":
                    members = sorted(list(set(next_group.get("members", []))))
                    self.current_group_firing_events = self.get_sequence_order(members, pattern_name)
                else: self.current_group_firing_events = []
            else: self.current_group_firing_events = []
            if self.software_step_timer: self.software_step_timer.start(1) # Minimal delay
            return

        if self.software_sequence_event_index >= len(self.current_group_firing_events):
            # Finished events for this group, move to the next group
            print(f"Finished events for group '{group_name}'. Moving to next group.")
            self.software_sequence_group_index += 1
            self.software_sequence_event_index = 0
            
            if self.software_sequence_group_index < len(self.ordered_groups_for_sw_execution):
                next_group = self.ordered_groups_for_sw_execution[self.software_sequence_group_index]
                pattern_name = next_group.get("pattern", "None")
                if pattern_name != "Pause" and pattern_name != "None":
                    members = sorted(list(set(next_group.get("members", []))))
                    self.current_group_firing_events = self.get_sequence_order(members, pattern_name)
                else: self.current_group_firing_events = []
            else:
                self.current_group_firing_events = []

            # The gap_ms here is from the group that JUST FINISHED. This is the delay before the next one starts.
            if self.software_step_timer: self.software_step_timer.start(max(1, gap_ms)) 
            return

        # Execute the current firing event
        current_event_cues_0_based = self.current_group_firing_events[self.software_sequence_event_index]
        
        if current_event_cues_0_based:
            cues_str = ", ".join([str(c+1) for c in sorted(current_event_cues_0_based)])
            print(f"Software fire: Group '{group_name}', Event {self.software_sequence_event_index + 1}, Cues [{cues_str}]")
            for cue_idx_to_fire in current_event_cues_0_based:
                if 0 <= cue_idx_to_fire < len(self.leds):
                    self.leds[cue_idx_to_fire].activate()
            QApplication.processEvents() # Process events to show flash
        else: # Should not happen if get_sequence_order filters empty events
            print(f"Software: Group '{group_name}', Event {self.software_sequence_event_index + 1} is empty, skipping.")

        self.software_sequence_event_index += 1
        if self.software_step_timer: self.software_step_timer.start(max(1, gap_ms)) # Schedule next event in this group
# END SECTION 14: MainWindow Class - Timeline and Sequence Methods

# START SECTION 15: MainWindow Class - Highlighting and Color Methods
    def highlight_group(self, group_name, active): # Unused currently, but can be kept
        # This method was for highlighting the group header in the list.
        # If re-enabled, ensure it finds the correct widget.
        pass

    def change_group_color(self, group_name):
        self._save_state_for_undo()
        group = self.get_group_by_name(group_name)
        if not group or group.get("pattern") == "Pause": # Cannot change color of Pause groups via dialog
            QMessageBox.critical(self, "Error", "Group not found or cannot change color of a pause entry.")
            return

        current_color = group.get("color", QColor(100,100,100))
        color = QColorDialog.getColor(current_color, self, "Select Group Color")

        if color.isValid():
            # Optional: Prevent pure red if it's reserved for activation
            # if color.red() > 200 and color.green() < 100 and color.blue() < 100:
            #     QMessageBox.warning(self, "Color Error", "Bright red is visually similar to activation flash!")
            #     # return # Or allow it

            group["color"] = color
            for idx in group.get("members", []):
                if 0 <= idx < len(self.leds):
                    self.leds[idx].set_color(color) # Update LED's stored color
                    # self.leds[idx].update_style() # set_color calls update_style

            # Update GroupHeaderWidget color indicator
            for i in range(self.group_list.count()):
                item = self.group_list.item(i)
                widget = self.group_list.itemWidget(item)
                if widget and isinstance(widget, GroupHeaderWidget) and widget.group_name == group_name:
                    widget.color_indicator.setStyleSheet(f"""
                        background-color: {color.name()};
                        border-radius: 15px;
                        border: 2px solid #fff;
                    """)
                    break
# END SECTION 15: MainWindow Class - Highlighting and Color Methods

# START SECTION 16: MainWindow Class - Sequence Upload (JSON) and Building Methods

    def build_sequence_json_payload(self):
        sequence_payload = {"groups": []}
        
        if hasattr(self, 'group_list'):
            for i in range(self.group_list.count()):
                item = self.group_list.item(i)
                widget = self.group_list.itemWidget(item)
                if not (widget and isinstance(widget, GroupHeaderWidget)): continue
                
                group_data = self.get_group_by_name(widget.group_name)
                if not group_data: continue

                pattern_name = group_data.get("pattern", "None")
                firing_events_0_based = [] 

                if pattern_name != "Pause" and pattern_name != "None":
                    # Ensure members are unique and sorted before processing for a pattern
                    zero_based_members = sorted(list(set(group_data.get("members", []))))
                    if zero_based_members: # Only get sequence if there are members
                        firing_events_0_based = self.get_sequence_order(zero_based_members, pattern_name)
                
                # Convert each event's 0-based indices to 1-based for JSON
                firing_steps_1_based_for_json = []
                for event_cues_0_based in firing_events_0_based:
                    # Ensure inner list is also sorted for consistent JSON, then convert to 1-based
                    firing_steps_1_based_for_json.append(sorted([idx + 1 for idx in event_cues_0_based]))

                json_group_entry = {
                    "name": group_data["name"],
                    "pattern": pattern_name, 
                    "gap_ms": group_data.get("gap", 500),
                    "steps": firing_steps_1_based_for_json # Changed "cues" to "steps"
                }
                sequence_payload["groups"].append(json_group_entry)
        
        return sequence_payload


    def upload_sequence(self):
        self.upload_confirmed_successful = False # Reset flag
        if self.software_only_mode:
            QMessageBox.information(self, "Info", "Upload is not applicable in Software Only mode.")
            return
        if not self.esp32_connected:
            QMessageBox.critical(self, "Error", "ESP32 not connected!")
            return
        if not self.groups:
            QMessageBox.warning(self, "Warning", "No groups or pauses defined to upload.")
            return

        # Build the JSON payload
        self.current_json_payload_dict = self.build_sequence_json_payload()
        if not self.current_json_payload_dict["groups"]:
            QMessageBox.warning(self, "Warning", "Sequence is empty. Nothing to upload.")
            return

        self.sequence_uploaded = False # Mark as not uploaded until verified
        self.update_button_states(is_uploading=True)

        # Show progress dialog
        if hasattr(self, 'upload_progress') and self.upload_progress:
            self.upload_progress.cancel() # Close any old one
        self.upload_progress = QProgressDialog("Preparing to upload JSON...", "Abort Upload", 0, 100, self)
        self.upload_progress.setWindowTitle("JSON Sequence Upload")
        self.upload_progress.setWindowModality(Qt.WindowModal)
        self.upload_progress.canceled.connect(self.abort_upload)
        self.upload_progress.setValue(0)
        self.upload_progress.setLabelText("Sending upload request to ESP32...")
        self.upload_progress.show()
        QApplication.processEvents()

        self.sequence_status_label.setText("Uploading JSON...")
        self.sequence_status_label.setStyleSheet("font-size: 20px; color: #FFFF00;") # Yellow

        # Send Command
        if not self.serial_worker.send_data(JSON_SEQUENCE_START_CMD + '\n'):
            self.abort_upload("Failed to send JSON upload start command.")


    def send_json_payload(self):
        """Called after ESP32 ACKs JSON_SEQUENCE_START_CMD."""
        if not hasattr(self, 'current_json_payload_dict') or not self.current_json_payload_dict:
            self.abort_upload("No JSON payload to send.")
            return

        try:
            json_string = json.dumps(self.current_json_payload_dict)
            
            # *** CHANGE IS HERE: The payload string itself now contains the required newlines ***
            payload_to_send = json_string + "\n" + JSON_SEQUENCE_END_CMD + "\n"

            if self.upload_progress:
                self.upload_progress.setLabelText(f"Sending JSON data ({len(payload_to_send)} bytes)...")
                self.upload_progress.setValue(50) # Mid-point
            QApplication.processEvents()

            if not self.serial_worker.send_data(payload_to_send):
                self.abort_upload("Failed to send JSON data.")
            else:
                print(f"Sent JSON payload ({len(payload_to_send)} bytes). Waiting for ESP32 parse confirmation.")
                if self.upload_progress:
                     self.upload_progress.setLabelText("Waiting for ESP32 confirmation...")
                     self.upload_progress.setValue(75)


        except TypeError as e:
            self.abort_upload(f"Error serializing sequence to JSON: {e}")
        except Exception as e:
            self.abort_upload(f"Unexpected error during JSON send: {e}")


    def abort_upload(self, message="Upload aborted by user"):
        print(f"Aborting upload: {message}")

        if hasattr(self, 'upload_progress') and self.upload_progress:
            self.upload_progress.cancel() # This will hide it
            self.upload_progress = None

        self.sequence_uploaded = False
        self.current_json_payload_dict = None # Clear stored payload

        self.sequence_status_label.setText("Upload Failed/Aborted")
        self.sequence_status_label.setStyleSheet("font-size: 20px; color: #FF0000;")

        if "Abort Upload" not in message and "user" not in message.lower():
             QMessageBox.warning(self, "Upload Problem", message)

        if self.serial_worker and self.esp32_connected:
            print("Sending ABORT_SEQUENCE to ESP32 due to upload abort/failure.")
            # *** CHANGE IS HERE: Add newline manually ***
            self.serial_worker.send_data("ABORT_SEQUENCE" + '\n')

        self.update_button_states()


    def update_button_states(self, is_counting_down_or_running=False, is_uploading=False):
        # Determine if any process is active
        is_busy = is_counting_down_or_running or \
                  is_uploading or \
                  (hasattr(self, 'software_step_timer') and self.software_step_timer.isActive()) or \
                  (self.countdown_label.text() == "Running...")

        # Cracker Initialization and Group Creation
        can_init_or_add_groups = not is_busy
        self.init_btn.setEnabled(can_init_or_add_groups)
        self.num_input.setEnabled(can_init_or_add_groups)
        self.add_single_btn.setEnabled(can_init_or_add_groups and bool(self.leds)) # Can add if already init
        self.add_five_btn.setEnabled(can_init_or_add_groups and bool(self.leds))
        self.reset_btn.setEnabled(can_init_or_add_groups)
        self.add_group_btn.setEnabled(can_init_or_add_groups and bool(self.leds))
        self.add_pause_btn.setEnabled(can_init_or_add_groups and bool(self.leds)) # Can add pause if system active
        self.auto_connect_btn.setEnabled(not self.software_only_mode)

        # Enable/disable group item menus
        for i in range(self.group_list.count()):
            item = self.group_list.item(i)
            widget = self.group_list.itemWidget(item)
            if widget and hasattr(widget, 'menu_button'):
                widget.menu_button.setEnabled(can_init_or_add_groups)


        # Upload Button
        can_upload = (not self.software_only_mode) and \
                     self.esp32_connected and \
                     bool(self.groups) and \
                     (not is_busy)
        self.upload_sequence_btn.setEnabled(can_upload)

        # Start Button
        can_start = False
        if not is_busy and bool(self.groups): # Cannot start if busy or no groups
            is_runnable_sequence = any(g.get("pattern") and \
                                   (g.get("pattern") != "None" or g.get("pattern") == "Pause") \
                                   for g in self.groups)
            if not is_runnable_sequence:
                can_start = False # No runnable groups
            elif self.software_only_mode:
                can_start = True # Software mode can start if groups exist and are runnable
            else: # Hardware mode
                can_start = self.sequence_uploaded and \
                            self.esp32_connected and \
                            self.sequence_status_label.text() == "Sequence: Uploaded & Ready"
        self.start_btn.setEnabled(can_start)

        # Serial Refresh Button
        self.refresh_btn.setEnabled(not is_busy and not self.software_only_mode)

        # Software Toggle Button
        self.software_toggle.setEnabled(not is_busy)

        self.arm_btn.setEnabled((not self.software_only_mode) and self.esp32_connected and not is_busy)

# END SECTION 16: MainWindow Class - Sequence Upload (JSON) and Building Methods

# START SECTION 16.5: Save and Load Methods
    def save_sequence_to_file(self):
        options = QFileDialog.Options()
        fileName, _ = QFileDialog.getSaveFileName(self, "Save Sequence File", "",
                                                  "Pyro Sequence Files (*.pyro);;All Files (*)", options=options)
        if not fileName:
            return

        if not fileName.endswith('.pyro'):
            fileName += '.pyro'
            
        save_data = {
            'cue_count': len(self.leds),
            'groups': self.groups 
        }

        try:
            with open(fileName, 'w') as f:
                def qcolor_serializer(obj):
                    if isinstance(obj, QColor):
                        return obj.name()
                    raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")

                json.dump(save_data, f, indent=4, default=qcolor_serializer)
            QMessageBox.information(self, "Success", f"Sequence saved to {fileName}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not save file: {e}")

    def open_sequence_from_file(self):
        options = QFileDialog.Options()
        fileName, _ = QFileDialog.getOpenFileName(self, "Open Sequence File", "",
                                                  "Pyro Sequence Files (*.pyro);;All Files (*)", options=options)
        if not fileName:
            return

        try:
            with open(fileName, 'r') as f:
                loaded_data = json.load(f)

            # 1. Reset the current GUI state (but don't send ABORT to ESP32 yet)
            self._reset_gui() 

            # 2. Set the number of cues
            cue_count = loaded_data.get('cue_count', 0)
            if cue_count > 0:
                self.num_input.setText(str(cue_count))
                self.init_crackers()
                # init_crackers calls reset_gui again, which is harmless.

            # 3. Rebuild the internal self.groups data structure
            loaded_groups = loaded_data.get('groups', [])
            for group in loaded_groups:
                if 'color' in group and group['color']:
                    group['color'] = QColor(group['color'])

            self.groups = loaded_groups # Overwrite the groups list

            # 4. Rebuild the visual GUI from the new self.groups data
            self.group_list.clear() # Clear the visual list
            for group_data in self.groups:
                # Add the group widget to the list, but DON'T append to self.groups
                # as we have already loaded it.
                self._add_group_to_gui_and_data(group_data, append_to_data=False) 
                
                # Update the colors of the member LEDs
                if group_data.get("pattern") != "Pause":
                    for member_idx in group_data.get("members", []):
                        if 0 <= member_idx < len(self.leds):
                            self.leds[member_idx].group = group_data["name"]
                            self.leds[member_idx].set_color(group_data["color"])

            # 5. Final UI updates
            self.generate_timeline()
            self.calculate_time()
            self.update_stats()
            QMessageBox.information(self, "Success", f"Sequence loaded from {fileName}")

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not load file: {e}")
# END SECTION 16.5: Save and Load Methods

# START SECTION 16.6: Undo/Redo Methods
    def _create_state_snapshot(self):
        """Creates a savable, deep copy of the current sequence state."""
        import copy
        # We must use deepcopy to ensure that nested lists (like members)
        # are also copied, not just referenced.
        snapshot = {
            'groups': copy.deepcopy(self.groups)
        }
        return snapshot

    def _restore_from_snapshot(self, snapshot):
        """Rebuilds the entire application state from a snapshot."""
        # Set a flag to prevent this function from creating new undo history
        self.is_restoring_state = True

        self.groups = snapshot.get('groups', [])
        
        # Rebuild the visual GUI from the new self.groups data
        self.group_list.clear()
        for group_data in self.groups:
            self._add_group_to_gui_and_data(group_data, append_to_data=False)
            if group_data.get("pattern") != "Pause":
                for member_idx in group_data.get("members", []):
                    if 0 <= member_idx < len(self.leds):
                        self.leds[member_idx].group = group_data["name"]
                        self.leds[member_idx].set_color(group_data["color"])
        
        # Final UI updates
        self.generate_timeline()
        self.calculate_time()
        self.update_stats()
        
        # Release the flag
        self.is_restoring_state = False

    def _save_state_for_undo(self):
        """Saves the current state to the undo history."""
        # If we are currently restoring a state, don't save another snapshot.
        # This prevents an infinite loop.
        if self.is_restoring_state:
            return

        snapshot = self._create_state_snapshot()
        self.editor_history.add_change(snapshot)
        self._update_undo_redo_actions() # Update the menu items

    def _update_undo_redo_actions(self):
        """Enables/disables the Undo/Redo menu actions based on history."""
        self.undoAction.setEnabled(bool(self.editor_history.history))
        self.redoAction.setEnabled(bool(self.editor_history.future))

    def undo_change(self):
        """Performs an Undo action."""
        if self.editor_history.history:
            # We need to save the *current* state to the redo stack
            # BEFORE we go back in time.
            current_state = self._create_state_snapshot()
            self.editor_history.future.append(current_state)

            # Now, get the previous state from the history
            last_state = self.editor_history.history.pop()
            self._restore_from_snapshot(last_state)
            self._update_undo_redo_actions()

    def redo_change(self):
        """Performs a Redo action."""
        if self.editor_history.future:
            # Save the current state back to the history stack
            current_state = self._create_state_snapshot()
            self.editor_history.history.append(current_state)

            # Get the next state from the future stack
            next_state = self.editor_history.future.pop()
            self._restore_from_snapshot(next_state)
            self._update_undo_redo_actions()

# END SECTION 16.6: Undo/Redo Methods


# (This is the end of the MainWindow class)
# START SECTION 17: Main Application and Stylesheet
if __name__ == "__main__":
    app = QApplication(sys.argv)

    
    # Your existing global stylesheet
    app.setStyleSheet("""
        QWidget {
            background-color: #2D2D2D;
            color: #FFFFFF;
            /* font-family can be set here if you like */
        }
        QLineEdit, QSpinBox, QComboBox {
            background-color: #404040;
            color: #FFFFFF;
            border: 1px solid #555555;
            border-radius: 4px;
            padding: 8px; /* Increased padding for more space */
        }
        QListWidget {
            background-color: #1A1A1A;
            border: 1px solid #333333;
            border-radius: 4px;
        }
        QPushButton {
            background-color: #505050;
            border: 1px solid #606060;
            border-radius: 4px;
            padding: 8px 12px;
        }
        QPushButton:hover { background-color: #606060; }
        QPushButton:pressed { background-color: #454545; }
        QPushButton:disabled { background-color: #353535; color: #777777; }
        QDialog {
            background-color: #2D2D2D;
        }
        /* ... other styles ... */
    """)

    window = MainWindow()
    window.show()
    sys.exit(app.exec_())
# END SECTION 17: Main Application and Stylesheet