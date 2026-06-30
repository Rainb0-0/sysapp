# -----------------------------------------------------------------------------
# schematic_view_tab.py - Enhanced with ORGANIZED PANELS and UNIFIED PDF Export
# 
# Key Improvements:
# 🎨 PANEL ORGANIZATION: Buttons organized in logical groups (View/File/Export/Stats)
# 📄 UNIFIED PDF EXPORT: PDF uses same high-quality PNG rendering method
# 🏷️ LABELED BUTTONS: All buttons have clear text labels for better UX
# 🎯 CONSISTENT STYLING: All export operations use identical quality standards
# -----------------------------------------------------------------------------
import sys
import os
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
                            QPushButton, QLabel, QGroupBox, QMessageBox, 
                            QInputDialog, QFileDialog, QProgressDialog)
from PyQt5.QtCore import Qt, QTimer, QRectF, QPointF
from PyQt5.QtGui import QFont, QPainter, QPixmap, QPainterPath, QTransform
from PyQt5.QtPrintSupport import QPrinter, QPrintDialog

# Project imports
from Schematic_View_tab.schematic_graphics import SchematicGraphicsScene, ZoomableGraphicsView
# --- imports (بالای فایل) ---
from Schematic_View_tab.mode.mode_integration import integrate_mode_system, debug_mode_system
from Schematic_View_tab.integration_fixes import quick_setup_enhanced_system
from Schematic_View_tab.routing_persistence import (
    save_enhanced_interface_data, load_enhanced_interface_data
)

# Import unified style system
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from styles.style_manager import (style_manager, register_widget, 
                                create_styled_button, auto_style_widget)
from styles.design_system import Colors, Typography, Spacing, BorderRadius
from styles.theme_manager import theme_manager, ThemeType

# Database functions
from database import get_current_project_id, get_connection, save_complete_layout, get_complete_layout
from auth_manager import auth

class SchematicViewTab(QWidget):
    """Main schematic view widget with integrated theme system and FIXED export functionality"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SchematicViewTab")
        self.setAttribute(Qt.WA_StyledBackground, True)

        # Initialize core components
        self.scene = SchematicGraphicsScene()
        self.view = ZoomableGraphicsView(self.scene)
        self.tree_selector = getattr(self.view, 'tree_selector', None)

        # Setup UI
        self.init_ui_components()
        self.init_ui()
        self.init_mode_system()
        self.connect_basic_signals()

        auth.auth_changed.connect(self.apply_access_policy)
        self.apply_access_policy()
        
        # Apply initial theme and connect theme changes
        self.apply_theme_styles()
        style_manager.theme_changed.connect(self.on_theme_changed)
        quick_setup_enhanced_system(self)

        # Initial refresh and verification
        self.refresh_all()
        QTimer.singleShot(300, self.final_initialization_check)

    def _ensure_project_selected(self):
        project_id = get_current_project_id()
        if project_id is None:
            QMessageBox.warning(self, "No Project Selected", 
                            "Please select or create a project first.")
            return False
        return True

    def init_ui_components(self):
        """Initialize UI components with CONSISTENT style system"""
        # Top control bar widgets
        self.control_widget = QWidget()
        
        # Main action buttons - all with same style for consistency
        self.tree_btn = create_styled_button("🌳 Tree", "normal")
        self.mode_btn = create_styled_button("🎛️ Mode", "normal") 
        self.load_btn = create_styled_button("📂 Load", "normal")
        self.save_btn = create_styled_button("💾 Save", "normal")
        
        # Export buttons - same style as other buttons for consistency
        self.export_pdf_btn = create_styled_button("📄 PDF", "normal")
        self.export_png_btn = create_styled_button("🖼️ PNG", "normal")
        
        # Statistics display buttons
        self.mass_display = QPushButton("⚖️ Mass: 0.00 kg")
        self.power_display = QPushButton("⚡ Power: 0.00 W")
        self.current_display = QPushButton("🔌 Current: 0.00 A")
        
        # Register stats buttons for custom styling
        register_widget(self.mass_display, "stats_display", "mass_display")
        register_widget(self.power_display, "stats_display", "power_display")
        register_widget(self.current_display, "stats_display", "current_display")

    def init_ui(self):
        """Initialize user interface layout"""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(8)

        self.setup_top_controls()

        main_layout.addWidget(self.control_widget)
        main_layout.addWidget(self.view)

        # Connect scene to display labels
        self.scene.total_mass_label = self.mass_display
        self.scene.total_power_label = self.power_display
        self.scene.total_current_label = self.current_display
    def setup_top_controls(self):
        """Setup top control bar with ORGANIZED PANELS"""
        self.control_widget.setFixedHeight(70)  # Increased height for panels
        
        main_layout = QHBoxLayout(self.control_widget)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(12)
        main_layout.setAlignment(Qt.AlignVCenter)

        # Set tooltips with more descriptive text
        self.tree_btn.setToolTip("Toggle Component Tree View")
        self.mode_btn.setToolTip("Toggle Mode Manager")
        self.load_btn.setToolTip("Load saved layout from database")
        self.save_btn.setToolTip("Save current layout to database")
        
        # Enhanced export tooltips
        self.export_pdf_btn.setToolTip("Export to PDF\n• Same quality as PNG\n• Perfect for documents\n• Professional format")
        self.export_png_btn.setToolTip("Export to PNG\n• High resolution bitmap\n• Great for documentation\n• Custom DPI settings")

        # === PANEL 1: VIEW CONTROLS ===
        view_panel = QGroupBox("View")
        view_layout = QHBoxLayout(view_panel)
        view_layout.setContentsMargins(8, 4, 8, 4)
        view_layout.setSpacing(4)
        
        view_layout.addWidget(self.tree_btn)
        view_layout.addWidget(self.mode_btn)
        
        # === PANEL 2: FILE OPERATIONS ===
        file_panel = QGroupBox("File")
        file_layout = QHBoxLayout(file_panel)
        file_layout.setContentsMargins(8, 4, 8, 4)
        file_layout.setSpacing(4)
        
        file_layout.addWidget(self.load_btn)
        file_layout.addWidget(self.save_btn)
        self.file_panel = file_panel
        
        # === PANEL 3: EXPORT OPERATIONS ===
        export_panel = QGroupBox("Export")
        export_layout = QHBoxLayout(export_panel)
        export_layout.setContentsMargins(8, 4, 8, 4)
        export_layout.setSpacing(4)
        
        export_layout.addWidget(self.export_pdf_btn)
        export_layout.addWidget(self.export_png_btn)
        
        # === PANEL 4: STATISTICS ===
        stats_panel = QGroupBox("Statistics")
        stats_layout = QHBoxLayout(stats_panel)
        stats_layout.setContentsMargins(8, 4, 8, 4)
        stats_layout.setSpacing(4)
        
        stats_layout.addWidget(self.mass_display)
        stats_layout.addWidget(self.power_display)
        stats_layout.addWidget(self.current_display)

        # Add panels to main layout
        main_layout.addWidget(view_panel)
        main_layout.addWidget(file_panel)
        main_layout.addWidget(export_panel)
        main_layout.addStretch()  # Push stats to right
        main_layout.addWidget(stats_panel)
        
        # Set focus policy for stats displays
        self.mass_display.setFocusPolicy(Qt.NoFocus)
        self.power_display.setFocusPolicy(Qt.NoFocus)
        self.current_display.setFocusPolicy(Qt.NoFocus)

    def apply_theme_styles(self):
        """Apply theme-based styles to all components"""
        # Main widget style
        main_style = f"""
            QWidget#SchematicViewTab {{
                background: {theme_manager.get_color('primary_dark')};
                border: 1px solid {theme_manager.get_color('primary_light')};
                border-radius: {BorderRadius.XLARGE};
            }}
            
            QMessageBox {{
                background: {theme_manager.get_color('primary_dark')}; 
                color: {theme_manager.get_color('text_primary')};    
                font-family: {Typography.FONT_FAMILY};
                font-size: {Typography.SIZE_MEDIUM};
            }}

            QMessageBox QLabel {{
                color: {theme_manager.get_color('text_primary')};   
            }}

            QMessageBox QPushButton {{
                background: {theme_manager.get_gradient("primary")};
                border: 1px solid {theme_manager.get_color('accent')};
                border-radius: {BorderRadius.LARGE};
                color: {theme_manager.get_color('text_primary')};
                font-family: {Typography.FONT_FAMILY};
                font-size: {Typography.SIZE_MEDIUM};
                font-weight: {Typography.WEIGHT_BOLD};
                padding: {Spacing.LG} {Spacing.XL};
            }}

            QProgressDialog {{
                background: {theme_manager.get_color('primary_dark')};
                color: {theme_manager.get_color('text_primary')};
                font-family: {Typography.FONT_FAMILY};
            }}
        """
        self.setStyleSheet(main_style)

        # Control widget style
        control_style = f"""
            QWidget {{
                background: {theme_manager.get_gradient("primary", "x1:0, y1:0, x2:1, y2:0")};
                border-radius: {BorderRadius.LARGE};
                border: 1px solid {theme_manager.get_color('primary_light')};
            }}
            
            QGroupBox {{
                font-weight: {Typography.WEIGHT_BOLD};
                font-size: {Typography.SIZE_SMALL};
                color: {theme_manager.get_color('text_secondary')};
                border: 1px solid {theme_manager.get_color('primary_light')};
                border-radius: {BorderRadius.MEDIUM};
                margin-top: 8px;
                padding-top: 4px;
                background: {theme_manager.get_color('primary_medium')};
            }}
            
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 8px;
                padding: 0 4px 0 4px;
                background: {theme_manager.get_color('primary_medium')};
            }}
        """
        self.control_widget.setStyleSheet(control_style)

        # Statistics display style
        stats_style = f"""
            QPushButton {{
                background: {theme_manager.get_color('primary_medium')};
                border: 1px solid {theme_manager.get_color('primary_light')};
                border-radius: {BorderRadius.LARGE};
                color: {theme_manager.get_color('text_primary')};
                font-family: {Typography.FONT_FAMILY};
                font-size: {Typography.SIZE_MEDIUM};
                font-weight: {Typography.WEIGHT_BOLD};
                padding: {Spacing.LG} {Spacing.XL};
                min-width: 160px;
            }}
            QPushButton:hover {{
                background: {theme_manager.get_gradient("hover", "x1:0, y1:0, x2:1, y2:1")};
                border: 1px solid {theme_manager.get_color('accent')};
                color: white;
            }}
        """
        
        # Register custom style for stats displays
        style_manager.set_custom_style("stats_display", stats_style)
        
        # Apply to existing stats buttons
        self.mass_display.setStyleSheet(stats_style)
        self.power_display.setStyleSheet(stats_style)
        self.current_display.setStyleSheet(stats_style)

    def on_theme_changed(self, theme_name):
        """Handle theme change signal"""
        self.apply_theme_styles()
        
        # Update scene background
        if hasattr(self.scene, 'apply_theme_background'):
            self.scene.apply_theme_background()
        
        # Update view if needed
        if hasattr(self.view, 'apply_theme_styles'):
            self.view.apply_theme_styles()

    def init_mode_system(self):
        try:
            self.mode_integrator = integrate_mode_system(self)
            if self.mode_integrator:
                self.mode_integrator.systemReady.connect(self._on_mode_system_ready)
                self.mode_integrator.systemError.connect(self._on_mode_system_error)
                self.mode_integrator.modeChanged.connect(self._on_mode_changed)
            else:
                QMessageBox.warning(self, "Mode System", "Mode system could not be initialized.")
        except Exception as e:
            QMessageBox.critical(self, "Mode System Error", f"Failed to initialize mode system:\n{e}")
            self.mode_manager = None
            self.mode_controller = None
            self.mode_graphics = None
            self.mode_integrator = None

    def _on_mode_system_ready(self):
        """Handle mode system ready signal"""
        if hasattr(self, 'mode_graphics') and self.mode_graphics:
            self.mode_graphics.refresh_modes()
        try:
            current_mode = getattr(self.mode_integrator, 'mode_manager', None)
            current_mode = getattr(current_mode, 'current_mode', None)
            self._on_mode_changed(current_mode)
        except Exception:
            self._set_file_ops_visible(True)
        self.apply_access_policy()

    def _on_mode_system_error(self, error_message):
        """Handle mode system error"""
        QMessageBox.warning(self, "Mode System Error", 
                          f"Mode system encountered an error:\n\n{error_message}")

    def _on_mode_changed(self, mode_name):
        in_mode = bool(mode_name)        
        self._set_file_ops_visible(not in_mode)  
        if hasattr(self, 'mode_btn') and self.mode_btn:
            self.mode_btn.setText("🎛️ Mode")


    def _set_file_ops_visible(self, visible: bool):
        if hasattr(self, 'file_panel') and self.file_panel:
            self.file_panel.setVisible(visible)
        if hasattr(self, 'save_btn') and self.save_btn:
            self.save_btn.setVisible(visible)
        if hasattr(self, 'load_btn') and self.load_btn:
            self.load_btn.setVisible(visible)


    def connect_basic_signals(self):
        """Connect basic UI signals"""
        # File operations
        self.save_btn.clicked.connect(self.save_schematic_layout)
        self.load_btn.clicked.connect(self.load_schematic_layout)
        
        # UI toggles
        self.tree_btn.clicked.connect(self.toggle_tree_visibility)
        self.mode_btn.clicked.connect(self.toggle_mode_visibility)
        
        # Export operations
        self.export_pdf_btn.clicked.connect(self.export_to_pdf)
        self.export_png_btn.clicked.connect(self.export_to_png)

    # =================================================================
    # FIXED EXPORT METHODS WITH PROPER FONT AND SHAPE HANDLING
    # =================================================================
    
    def export_to_pdf(self):
        """Export schematic scene to PDF using PNG conversion method"""
        try:
            # Get save path from user
            file_path, _ = QFileDialog.getSaveFileName(
                self,
                "Export Schematic to PDF",
                f"schematic_export_{self._get_timestamp()}.pdf",
                "PDF Files (*.pdf);;All Files (*)"
            )
            
            if not file_path:
                return  # User cancelled
            
            # Create progress dialog
            progress = QProgressDialog("Creating PDF from high-quality image...", "Cancel", 0, 100, self)
            progress.setWindowModality(Qt.WindowModal)
            progress.show()
            progress.setValue(10)
            
            # Get scene bounds and validate
            scene_rect = self.scene.itemsBoundingRect()
            if scene_rect.isEmpty():
                QMessageBox.warning(self, "Export Warning", 
                                  "No items found in the scene to export.\n\n"
                                  "Please add some components first.")
                progress.close()
                return
            
            progress.setLabelText("Generating high-quality image...")
            progress.setValue(20)
            
            # Create high-quality PNG in memory (same method as PNG export)
            margin = 50
            export_rect = scene_rect.adjusted(-margin, -margin, margin, margin)
            
            # Use high resolution for crisp PDF
            resolution = 300  # Fixed high resolution for PDF
            scale_factor = resolution / 96.0
            pixmap_width = int(export_rect.width() * scale_factor)
            pixmap_height = int(export_rect.height() * scale_factor)
            
            progress.setValue(30)
            
            # Create high-resolution pixmap with WHITE background
            pixmap = QPixmap(pixmap_width, pixmap_height)
            pixmap.fill(Qt.white)  # Professional white background
            
            progress.setLabelText("Rendering schematic at high quality...")
            progress.setValue(40)
            
            # Setup painter with MAXIMUM quality settings
            painter = QPainter(pixmap)
            
            # Enable ALL quality improvements
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setRenderHint(QPainter.TextAntialiasing, True)
            painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
            painter.setRenderHint(QPainter.HighQualityAntialiasing, True)
            painter.setRenderHint(QPainter.NonCosmeticDefaultPen, True)
            
            # Render scene to pixmap with proper scaling
            target_rect = QRectF(0, 0, pixmap_width, pixmap_height)
            self.scene.render(painter, target_rect, export_rect, Qt.KeepAspectRatio)
            
            painter.end()
            progress.setValue(60)
            
            progress.setLabelText("Creating PDF document...")
            progress.setValue(70)
            
            # Now create PDF with the rendered image
            printer = QPrinter(QPrinter.HighResolution)
            printer.setOutputFormat(QPrinter.PdfFormat)
            printer.setOutputFileName(file_path)
            printer.setPageSize(QPrinter.A4)  # Standard A4 size
            printer.setOrientation(QPrinter.Landscape if pixmap_width > pixmap_height else QPrinter.Portrait)
            printer.setResolution(300)
            
            # Setup PDF painter
            pdf_painter = QPainter()
            if not pdf_painter.begin(printer):
                QMessageBox.critical(self, "Export Error", 
                                   "Could not initialize PDF painter.\n\n"
                                   "Please check if the file is not open in another application.")
                progress.close()
                return
            
            progress.setValue(80)
            
            # Get page dimensions
            page_rect = printer.pageRect(QPrinter.DevicePixel)
            
            # Calculate scaling to fit page while maintaining aspect ratio
            scale_x = page_rect.width() / pixmap_width
            scale_y = page_rect.height() / pixmap_height
            scale = min(scale_x, scale_y, 1.0)  # Don't scale up
            
            # Calculate centered position
            scaled_width = pixmap_width * scale
            scaled_height = pixmap_height * scale
            x = (page_rect.width() - scaled_width) / 2
            y = (page_rect.height() - scaled_height) / 2
            
            progress.setLabelText("Embedding image into PDF...")
            progress.setValue(90)
            
            # Draw the pixmap into PDF
            target_rect_pdf = QRectF(x, y, scaled_width, scaled_height)
            pdf_painter.drawPixmap(target_rect_pdf, pixmap, QRectF(pixmap.rect()))
            
            pdf_painter.end()
            progress.setValue(100)
            progress.close()
            
            # Calculate file size
            file_size = os.path.getsize(file_path) / (1024 * 1024)  # MB
            
            # Success message with details
            QMessageBox.information(
                self, 
                "PDF Export Successful", 
                f"📄 PDF Export Complete!\n\n"
                f"📁 File: {os.path.basename(file_path)}\n"
                f"📐 Format: A4 {'Landscape' if pixmap_width > pixmap_height else 'Portrait'}\n"
                f"🎯 Quality: 300 DPI (Same as PNG)\n"
                f"📏 Scale: {scale:.2f}x (Fit to page)\n"
                f"💾 Size: {file_size:.1f} MB\n\n"
                f"✨ Features:\n"
                f"  • Identical quality to PNG export\n"
                f"  • Professional document format\n"
                f"  • Perfect for sharing and printing\n"
                f"  • Universal compatibility\n\n"
                f"🎉 Ready for professional use!"
            )
            
        except Exception as e:
            if 'progress' in locals():
                progress.close()
            QMessageBox.critical(self, "PDF Export Error", 
                               f"Failed to export PDF:\n\n{str(e)}\n\n"
                               f"💡 Troubleshooting:\n"
                               f"• Close the PDF if it's open elsewhere\n"
                               f"• Check disk space\n"
                               f"• Try a different location")

    def export_to_png(self):
        """Export schematic scene to high-quality PNG with FIXED rendering"""
        try:
            # Get save path from user
            file_path, _ = QFileDialog.getSaveFileName(
                self,
                "Export Schematic to PNG",
                f"schematic_export_{self._get_timestamp()}.png",
                "PNG Files (*.png);;All Files (*)"
            )
            
            if not file_path:
                return  # User cancelled
            
            # Ask for resolution with better options
            resolution_options = [
                "150 DPI - Web/Email (Good quality, small file)",
                "300 DPI - Standard Print (High quality, recommended)", 
                "600 DPI - Professional Print (Highest quality, large file)",
                "Custom DPI - Enter your own value"
            ]
            
            resolution_choice, ok = QInputDialog.getItem(
                self,
                "Export Resolution",
                "Choose export resolution:\n\n"
                "Higher DPI = Better quality but larger file size",
                resolution_options,
                1,  # Default to 300 DPI
                False
            )
            
            if not ok:
                return
                
            # Parse resolution
            if "150 DPI" in resolution_choice:
                resolution = 150
            elif "300 DPI" in resolution_choice:
                resolution = 300
            elif "600 DPI" in resolution_choice:
                resolution = 600
            else:  # Custom
                resolution, ok = QInputDialog.getInt(
                    self,
                    "Custom Resolution",
                    "Enter DPI (dots per inch):",
                    300,  # default
                    72,   # minimum
                    1200  # maximum
                )
                if not ok:
                    return
            
            # Create progress dialog
            progress = QProgressDialog("Preparing PNG export...", "Cancel", 0, 100, self)
            progress.setWindowModality(Qt.WindowModal)
            progress.show()
            progress.setValue(10)
            
            # Get scene bounds
            scene_rect = self.scene.itemsBoundingRect()
            if scene_rect.isEmpty():
                QMessageBox.warning(self, "Export Warning", 
                                  "No items found in the scene to export.\n\n"
                                  "Please add some components first.")
                progress.close()
                return
            
            # Add margin for better presentation
            margin = 50
            export_rect = scene_rect.adjusted(-margin, -margin, margin, margin)
            
            progress.setLabelText("Calculating image dimensions...")
            progress.setValue(20)
            
            # Calculate pixmap size based on resolution
            # Standard screen DPI is 96, so we scale accordingly
            scale_factor = resolution / 96.0
            pixmap_width = int(export_rect.width() * scale_factor)
            pixmap_height = int(export_rect.height() * scale_factor)
            
            # Validate image size (prevent memory issues)
            max_pixels = 50000000  # 50 megapixels limit
            total_pixels = pixmap_width * pixmap_height
            
            if total_pixels > max_pixels:
                scale_down = (max_pixels / total_pixels) ** 0.5
                pixmap_width = int(pixmap_width * scale_down)
                pixmap_height = int(pixmap_height * scale_down)
                actual_dpi = int(resolution * scale_down)
                QMessageBox.information(
                    self, 
                    "Resolution Adjusted",
                    f"Image size was too large for memory.\n\n"
                    f"Adjusted to {actual_dpi} DPI\n"
                    f"Final size: {pixmap_width}×{pixmap_height} pixels"
                )
                resolution = actual_dpi
            
            progress.setValue(30)
            
            # Create high-resolution pixmap with WHITE background
            pixmap = QPixmap(pixmap_width, pixmap_height)
            pixmap.fill(Qt.white)  # Professional white background
            
            progress.setLabelText("Setting up high-quality renderer...")
            progress.setValue(40)
            
            # Setup painter with MAXIMUM quality settings
            painter = QPainter(pixmap)
            
            # Enable ALL quality improvements
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setRenderHint(QPainter.TextAntialiasing, True)
            painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
            painter.setRenderHint(QPainter.HighQualityAntialiasing, True)
            painter.setRenderHint(QPainter.NonCosmeticDefaultPen, True)
            
            progress.setValue(60)
            
            # Render scene to pixmap with proper scaling
            target_rect = QRectF(0, 0, pixmap_width, pixmap_height)
            
            progress.setLabelText("Rendering high-resolution image...")
            progress.setValue(70)
            
            # Render with optimal quality
            self.scene.render(painter, target_rect, export_rect, Qt.KeepAspectRatio)
            
            progress.setValue(85)
            
            painter.end()
            
            progress.setLabelText("Saving PNG file...")
            progress.setValue(90)
            
            # Save pixmap with maximum quality
            if not pixmap.save(file_path, "PNG", 100):  # 100 = highest quality
                QMessageBox.critical(self, "Export Error", 
                                   "Failed to save PNG file.\n\n"
                                   "Please check disk space and file permissions.")
                progress.close()
                return
            
            progress.setValue(100)
            progress.close()
            
            # Calculate file size and show detailed results
            file_size = os.path.getsize(file_path) / (1024 * 1024)  # MB
            
            # Success message with comprehensive details
            QMessageBox.information(
                self, 
                "PNG Export Successful", 
                f"🖼️ High-Quality PNG Export Complete!\n\n"
                f"📁 File: {os.path.basename(file_path)}\n"
                f"📐 Resolution: {resolution} DPI\n"
                f"📏 Dimensions: {pixmap_width:,} × {pixmap_height:,} pixels\n"
                f"💾 File Size: {file_size:.1f} MB\n"
                f"🎨 Background: Professional White\n\n"
                f"✨ Quality Features:\n"
                f"  • Anti-aliased text and shapes\n"
                f"  • Smooth lines and curves\n"
                f"  • High-resolution rendering\n"
                f"  • Optimized for documentation\n\n"
                f"📋 Perfect for reports and presentations!"
            )
            
        except Exception as e:
            if 'progress' in locals():
                progress.close()
            QMessageBox.critical(self, "PNG Export Error", 
                               f"Failed to export PNG:\n\n{str(e)}\n\n"
                               f"💡 Troubleshooting:\n"
                               f"• Try lower resolution\n"
                               f"• Check available memory\n"
                               f"• Ensure sufficient disk space")

    def _get_timestamp(self):
        """Get current timestamp for filename"""
        from datetime import datetime
        return datetime.now().strftime("%Y%m%d_%H%M%S")

    # =================================================================
    # UI TOGGLE METHODS (بدون تغییر)
    # =================================================================
    
    def toggle_tree_visibility(self):
        """Toggle component tree visibility"""
        if self.view.tree_selector.isVisible():
            self.view.tree_selector.hide()
            self.tree_btn.setText("🌳 Tree")
        else:
            self.view.tree_selector.show()
            self.tree_btn.setText("🌳 Tree ✓")
            if hasattr(self.view.tree_selector, 'refresh_tree'):
                self.view.tree_selector.refresh_tree()
            
        self.update_mode_graphics_position()

    def toggle_mode_visibility(self):
        """Toggle mode graphics visibility"""
        if not getattr(self, 'mode_graphics', None):
            QMessageBox.information(self, "Mode System", 
                                "Mode system did not initialize properly.\n\n"
                                "Please check mode_integration.py and database connectivity.")
            return

        if self.mode_graphics.isVisible():
            self.mode_graphics.hide()
            self.mode_btn.setText("🎛️ Mode")
        else:
            self.mode_graphics.show()
            self.mode_btn.setText("🎛️ Mode ✓")
            if hasattr(self.mode_graphics, 'refresh_modes'):
                self.mode_graphics.refresh_modes()
            
        self.update_mode_graphics_position()

    def update_mode_graphics_position(self):
        """Update mode graphics position based on tree visibility"""
        if not hasattr(self, 'mode_graphics') or not self.mode_graphics or not self.mode_graphics.isVisible():
            return
            
        tree_visible = self.view.tree_selector.isVisible()
        
        if tree_visible:
            # Position below the tree selector
            tree_rect = self.view.tree_selector.geometry()
            new_x = tree_rect.left()
            new_y = tree_rect.bottom() + 15
            self.mode_graphics.move(new_x, new_y)
        else:
            # Position where tree would be
            tree_rect = self.view.tree_selector.geometry()
            new_x = tree_rect.left()
            new_y = tree_rect.top()
            self.mode_graphics.move(new_x, new_y)

    # =================================================================
    # SAVE/LOAD METHODS (بدون تغییر)
    # =================================================================

    def save_schematic_layout(self):
        """Save schematic layout to database"""
        try:
            # Collect module positions WITH SIZE
            module_positions = []
            if hasattr(self.scene, 'module_graphics_items'):
                for mod_id, item in self.scene.module_graphics_items.items():
                    pos = item.pos()
                    # FIXED: Use actual _rect dimensions, not boundingRect which includes margins
                    actual_width = item._rect.width()
                    actual_height = item._rect.height()
                    module_positions.append((pos.x(), pos.y(), mod_id, actual_width, actual_height))

            # Map connectors with unique database IDs - RELATIVE POSITIONS
            connector_positions = []
            if hasattr(self.scene, 'connector_graphics_items'):
                for connector_id, item in self.scene.connector_graphics_items.items():
                    parent = item.parentItem()
                    if parent:
                        relative_pos = item.pos() - parent.pos()
                    else:
                        relative_pos = item.pos()
                    rect = item.boundingRect()
                    # Get connector side
                    side = getattr(item, 'side', 'top')
                    connector_positions.append((relative_pos.x(), relative_pos.y(), 
                                            rect.width(), rect.height(), side, connector_id))

            # UPDATE PIN ORDER in database
            if not self._ensure_project_selected():
                QMessageBox.warning(self, "Save Error", "No project selected.")
                return
                
            project_id = get_current_project_id()
            
            try:
                with get_connection() as conn:
                    cur = conn.cursor()
                    
                    for connector_id, item in self.scene.connector_graphics_items.items():
                        for pin_idx, pin_name in enumerate(item.pin_names):
                            cur.execute(
                                "UPDATE pins SET pin_number = %s WHERE connector_id = %s AND name = %s AND project_id = %s",
                                (pin_idx, connector_id, pin_name, project_id)
                            )
                    
                    conn.commit()
            except Exception as e:
                QMessageBox.warning(self, "Database Error", f"Failed to update pin order: {str(e)}")

            # Map interfaces with unique database IDs
            interface_positions = []
            if hasattr(self.scene, 'interface_graphics_items'):
                for interface_id, item in self.scene.interface_graphics_items.items():
                    x = y = 0.0
                    rotation = 0.0
                    if hasattr(item, 'pos'):
                        p = item.pos()
                        x, y = p.x(), p.y()
                    if hasattr(item, 'rotation'):
                        try:
                            rotation = float(item.rotation())
                        except Exception:
                            rotation = 0.0
                    interface_positions.append((x, y, rotation, interface_id))

            # Collect routing points with MANUAL OVERRIDE preservation
            interface_points_data = {}
            if hasattr(self.scene, 'interface_graphics_items'):
                for interface_id, item in self.scene.interface_graphics_items.items():
                    points = []
                    if hasattr(item, 'get_routing_points'):
                        points = item.get_routing_points()
                    elif hasattr(item, 'path_points'):
                        points = item.path_points
                    elif hasattr(item, 'points'):
                        points = item.points
                    elif hasattr(item, 'line') and hasattr(item.line(), 'p1') and hasattr(item.line(), 'p2'):
                        ln = item.line()
                        points = [(ln.p1().x(), ln.p1().y()), (ln.p2().x(), ln.p2().y())]
                    if points:
                        interface_points_data[interface_id] = points

            # Save everything
            if module_positions or connector_positions or interface_positions or interface_points_data:
                save_complete_layout(module_positions, connector_positions, interface_positions, interface_points_data)
                
                success_msg = f"💾 Saved layout successfully:\n"
                success_msg += f"• {len(module_positions)} modules\n"
                success_msg += f"• {len(connector_positions)} connectors\n" 
                success_msg += f"• {len(interface_positions)} connections\n"
                success_msg += f"• {sum(len(points) for points in interface_points_data.values())} routing points\n"
                success_msg += f"\n🎉 Ready for app restart!"
                
                QMessageBox.information(self, "Save Successful", success_msg)
            else:
                QMessageBox.warning(self, "No Items", "No items found to save.")
            
        except Exception as e:
            QMessageBox.critical(self, "Save Error", f"Could not save layout.\n\nError: {e}")
    
    def load_schematic_layout(self):
        """Refresh current selection with saved positions"""
        try:
            if hasattr(self.view, 'tree_selector'):
                # Get current selection and refresh it (which will auto-load positions)
                current_selection = self.view.tree_selector.get_checked_ids()
                if any(current_selection.values()):
                    self.scene.update_display_from_selection(current_selection)
                    QMessageBox.information(self, "Layout Refreshed", 
                                        "Current selection has been refreshed with saved positions.")
                else:
                    QMessageBox.information(self, "No Selection", 
                                        "Please select some components in the tree first.")
            else:
                QMessageBox.warning(self, "Tree Not Available", 
                                "Component tree is not available.")
                
        except Exception as e:
            QMessageBox.critical(self, "Refresh Error", f"Could not refresh layout.\n\nError: {e}")

        self.apply_access_policy()

    # =================================================================
    # UTILITY METHODS (بدون تغییر)
    # =================================================================

    def final_initialization_check(self):
        """Final initialization verification"""
        components = {
            'scene': hasattr(self, 'scene') and self.scene is not None,
            'view': hasattr(self, 'view') and self.view is not None,
            'mode_integrator': hasattr(self, 'mode_integrator') and self.mode_integrator is not None,
            'mode_manager': hasattr(self, 'mode_manager'),
            'mode_controller': hasattr(self, 'mode_controller'),
            'mode_graphics': hasattr(self, 'mode_graphics')
        }

    def refresh_all(self):
        """Refresh scene and update all statistics"""
        try:
            if hasattr(self.view, 'tree_selector'):
                selection = self.view.tree_selector.get_checked_ids()
                self.scene.update_display_from_selection(selection)
            
            # Force update of all statistics
            self.scene.update_all_statistics()
            
        except Exception as e:
            print(f"Error refreshing: {e}")
            self.scene.update_display_from_selection({})
        self.apply_access_policy()

    def update_stats_labels(self, total_mass, total_power, total_current=None):
        """Update statistics labels.
        If total_current is None, recompute from the scene to avoid NameError and keep UI in sync.
        """
        self.mass_display.setText(f"⚖️ Mass: {total_mass:.2f} kg")
        self.power_display.setText(f"⚡ Power: {total_power:.2f} W")
        if total_current is None:
            try:
                total_current, _ = self.scene.calculate_total_current()
            except Exception:
                total_current = 0.0
        self.current_display.setText(f"🔌 Current: {total_current:.2f} A")

    # =================================================================
    # THEME UTILITIES (بدون تغییر)
    # =================================================================
    
    def set_theme(self, theme_type: ThemeType):
        """Set theme for schematic view"""
        style_manager.apply_theme(theme_type)

    def get_current_theme(self) -> ThemeType:
        """Get current theme"""
        return theme_manager.current_theme

    # =================================================================
    # EVENT HANDLERS (بدون تغییر)
    # =================================================================

    def showEvent(self, event):
        """Handle tab show event"""
        super().showEvent(event)
        if hasattr(self.view, "tree_selector"):
            self.view.tree_selector.refresh_tree()
        self.refresh_all()
        self.update_mode_graphics_position()

    def resizeEvent(self, event):
        """Handle resize event"""
        super().resizeEvent(event)
        self.update_mode_graphics_position()

    def set_read_only(self, flag: bool):
        # Disable scene interactions globally if you have a scene reference:
        sc = getattr(self, "scene", None) or getattr(self, "graphics_scene", None)
        if sc:
            for item in sc.items():
                # ModuleGraphics
                if hasattr(item, "set_read_only"):
                    item.set_read_only(flag)
                # SmartOrthogonalConnector (segments are QGraphicsLineItem; we keep it via parent object)
                if hasattr(item, "set_read_only"):
                    item.set_read_only(flag)

    def apply_access_policy(self):
        """
        Enforce access policy on Schematic tab:
        - system: full control (move/resize modules, edit/connectors, save enabled)
        - others: view-only (no move/resize/edit), Save disabled. Tree/Mode/Load/Export stay enabled.
        """
        try:
            is_sys = bool(getattr(auth, "is_system", lambda: False)())
        except Exception:
            is_sys = False

        # --- Buttons: Save only for system ---
        if hasattr(self, "save_btn") and self.save_btn:
            self.save_btn.setEnabled(is_sys)
            # keep visible for everyone; only disabled for non-system

        # Load/Tree/Mode/Exports remain enabled for everyone (do not touch)

        # --- Graphics read-only: lock scene objects for non-system ---
        read_only = not is_sys

        # Lock all modules
        try:
            modules = getattr(self.scene, "module_graphics_items", None)
            if isinstance(modules, dict):
                for _id, mod_item in modules.items():
                    if hasattr(mod_item, "set_read_only"):
                        mod_item.set_read_only(read_only)
        except Exception as e:
            print(f"[policy] modules lock error: {e}")

        # Lock all interface (connection) graphics
        try:
            interfaces = getattr(self.scene, "interface_graphics_items", None)
            if isinstance(interfaces, dict):
                for _id, edge in interfaces.items():
                    # SmartOrthogonalConnector wrapper may be stored as edge
                    if hasattr(edge, "set_read_only"):
                        edge.set_read_only(read_only)
        except Exception as e:
            print(f"[policy] interfaces lock error: {e}")

        # If there is any "mode" graphics/controller, keep it view-only as well
        try:
            if hasattr(self, "mode_graphics") and self.mode_graphics:
                # if your mode graphics has a setter for RO, call it here
                if hasattr(self.mode_graphics, "set_read_only"):
                    self.mode_graphics.set_read_only(read_only)
        except Exception as e:
            print(f"[policy] mode lock error: {e}")




# Test runner
if __name__ == "__main__":
    from PyQt5.QtWidgets import QApplication, QMainWindow
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
    from database import init_db
    init_db()

    app = QApplication(sys.argv)
    font = QFont("Roboto Mono", 15)
    app.setFont(font)
    app.setStyle('Fusion')

    window = QMainWindow()
    window.setWindowTitle("🎨 Enhanced Schematic View - Organized Panels & Unified PDF Export")
    window.setGeometry(100, 100, 1400, 900)

    schematic_tab = SchematicViewTab()
    window.setCentralWidget(schematic_tab)

    # Theme switching example
    from PyQt5.QtWidgets import QMenuBar
    menubar = window.menuBar()
    theme_menu = menubar.addMenu('Themes')
    
    theme_actions = {}
    for theme in ThemeType:
        action = theme_menu.addAction(theme.value.title())
        action.triggered.connect(lambda checked, t=theme: schematic_tab.set_theme(t))
        theme_actions[theme] = action

    window.show()
    sys.exit(app.exec_())
