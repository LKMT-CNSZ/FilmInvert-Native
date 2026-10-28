"""Modal non-destructive crop, quarter-turn and straightening workbench."""
import numpy as np
from PySide6.QtCore import Qt, Signal, QPointF, QRectF, QTimer
from PySide6.QtGui import QColor, QPen, QPolygonF, QPainterPath
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox, QDoubleSpinBox, QSplitter
import geometry
import perspective
from native_widgets import ImageCanvas, ParameterRow


class CropCanvas(ImageCanvas):
    cropChanged = Signal(object)

    def __init__(self):
        super().__init__()
        self.setAccessibleName('裁切画布：拖动角点缩放，框内移动，框外重新框选')
        self.setDragMode(ImageCanvas.DragMode.NoDrag)
        self.setAcceptDrops(False)
        self.crop = geometry.FULL.copy()
        self.ratio = None
        self.drag_kind = None
        self.grid_divisions=3

    def set_crop(self, value, emit=False):
        self.crop = geometry.normalize_crop(value)
        self.viewport().update()
        if emit:
            self.cropChanged.emit(self.crop.copy())

    def crop_rect(self):
        bounds = self.item.boundingRect()
        x0, y0, x1, y1 = self.crop
        return QRectF(x0 * bounds.width(), y0 * bounds.height(), (x1-x0) * bounds.width(), (y1-y0) * bounds.height())

    def point(self, event):
        point = self.mapToScene(event.position().toPoint())
        rect = self.item.boundingRect()
        return np.clip([point.x()/max(1,rect.width()), point.y()/max(1,rect.height())], 0, 1)

    def selection(self, anchor, point):
        dx, dy = point[0]-anchor[0], point[1]-anchor[1]
        if self.ratio:
            rect = self.item.boundingRect()
            ratio = self.ratio * rect.height() / rect.width()
            width, height = max(abs(dx), abs(dy)*ratio), max(abs(dy), abs(dx)/ratio)
            sx, sy = 1 if dx >= 0 else -1, 1 if dy >= 0 else -1
            limit_x, limit_y = (1-anchor[0] if sx>0 else anchor[0]), (1-anchor[1] if sy>0 else anchor[1])
            factor = min(1., limit_x/max(width,1e-9), limit_y/max(height,1e-9))
            point = [anchor[0]+sx*width*factor, anchor[1]+sy*height*factor]
        x0, x1 = sorted([float(anchor[0]),float(point[0])])
        y0, y1 = sorted([float(anchor[1]),float(point[1])])
        if x1-x0>=.005 and y1-y0>=.005:
            self.set_crop([x0,y0,x1,y1],True)

    def mousePressEvent(self, event):
        if event.button()!=Qt.MouseButton.LeftButton or self.item.pixmap().isNull():
            return super().mousePressEvent(event)
        self.start = self.point(event)
        self.start_crop = self.crop.copy()
        rect = self.crop_rect()
        corners = [rect.topLeft(),rect.topRight(),rect.bottomRight(),rect.bottomLeft()]
        click = event.position()
        for i,corner in enumerate(corners):
            pixel = self.mapFromScene(corner)
            if abs(pixel.x()-click.x())<=12 and abs(pixel.y()-click.y())<=12:
                opposite = corners[(i+2)%4]
                bounds = self.item.boundingRect()
                self.anchor = [opposite.x()/bounds.width(), opposite.y()/bounds.height()]
                self.drag_kind = 'resize'
                return
        if rect.contains(self.mapToScene(event.position().toPoint())) and self.crop!=geometry.FULL:
            self.drag_kind = 'move'
        else:
            self.anchor = self.start
            self.drag_kind = 'resize'

    def mouseMoveEvent(self, event):
        if self.drag_kind=='move':
            dx, dy = self.point(event)-self.start
            x0,y0,x1,y1 = self.start_crop
            dx,dy = np.clip(dx,-x0,1-x1),np.clip(dy,-y0,1-y1)
            self.set_crop([x0+dx,y0+dy,x1+dx,y1+dy],True)
        elif self.drag_kind=='resize':
            self.selection(self.anchor,self.point(event))
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:
            self.drag_kind = None
        else:
            super().mouseReleaseEvent(event)

    def drawForeground(self,painter,rect):
        if self.item.pixmap().isNull():
            return
        image = self.item.boundingRect()
        crop = self.crop_rect()
        shade = QColor(0,0,0,155)
        for area in (QRectF(0,0,image.width(),crop.top()),
                     QRectF(0,crop.bottom(),image.width(),image.height()-crop.bottom()),
                     QRectF(0,crop.top(),crop.left(),crop.height()),
                     QRectF(crop.right(),crop.top(),image.width()-crop.right(),crop.height())):
            painter.fillRect(area,shade)
        pen = QPen(QColor('#111111'),3.5);pen.setCosmetic(True);painter.setPen(pen);painter.drawRect(crop)
        pen.setColor(QColor('#ededed'));pen.setWidthF(1.5);painter.setPen(pen)
        painter.drawRect(crop)
        pen.setColor(QColor(255,255,255,150));pen.setWidthF(.8);painter.setPen(pen)
        for i in range(1,self.grid_divisions):
            x,y = crop.left()+crop.width()*i/self.grid_divisions,crop.top()+crop.height()*i/self.grid_divisions
            painter.drawLine(QPointF(x,crop.top()),QPointF(x,crop.bottom()))
            painter.drawLine(QPointF(crop.left(),y),QPointF(crop.right(),y))
        radius = 4/max(.001,self.transform().m11())
        for corner in (crop.topLeft(),crop.topRight(),crop.bottomRight(),crop.bottomLeft()):
            painter.fillRect(QRectF(corner.x()-radius,corner.y()-radius,2*radius,2*radius),QColor('#ffffff'))


class LineCanvas(CropCanvas):
    lineChanged=Signal(object)
    def __init__(self):
        super().__init__();self.line_mode=False;self.line=None;self.line_drag=None
    def mousePressEvent(self,event):
        if not self.line_mode or event.button()!=Qt.MouseButton.LeftButton:return super().mousePressEvent(event)
        point=self.point(event);self.line_drag=1
        if self.line:
            rect=self.item.boundingRect()
            for i,p in enumerate(self.line):
                screen=self.mapFromScene(QPointF(p[0]*rect.width(),p[1]*rect.height()))
                if (screen-event.position().toPoint()).manhattanLength()<18:self.line_drag=i;break
            else:self.line=[point.tolist(),point.tolist()]
        else:self.line=[point.tolist(),point.tolist()]
        self.viewport().update()
    def mouseMoveEvent(self,event):
        if self.line_mode and self.line_drag is not None:
            self.line[self.line_drag]=self.point(event).tolist();self.lineChanged.emit(self.line)
            self.viewport().update();return
        super().mouseMoveEvent(event)
    def mouseReleaseEvent(self,event):
        if self.line_mode and event.button()==Qt.MouseButton.LeftButton:
            if self.line_drag is not None:
                self.line[self.line_drag]=self.point(event).tolist();self.lineChanged.emit(self.line)
            self.line_drag=None;self.viewport().update();return
        super().mouseReleaseEvent(event)
    def drawForeground(self,painter,rect):
        super().drawForeground(painter,rect)
        if not self.line_mode or not self.line:return
        bounds=self.item.boundingRect();points=[QPointF(p[0]*bounds.width(),p[1]*bounds.height()) for p in self.line]
        pen=QPen(QColor('#bbbbbb'),2);pen.setCosmetic(True);painter.setPen(pen);painter.drawLine(*points)
        painter.setBrush(QColor('#bbbbbb'));radius=5/max(.001,self.transform().m11())
        for point in points:painter.drawEllipse(point,radius,radius)


class MultiLineCanvas(LineCanvas):
    selectionChanged=Signal(int)
    def __init__(self):
        super().__init__();self.reference_lines=[];self.selected_line=-1
        self.new_axis='horizontal';self.invalid_lines=set()
        self.setAccessibleName('多参考线校直：空白处拖动添加，点击线段选中，拖动端点调整')

    def select_line(self,index):
        self.selected_line=index if 0<=index<len(self.reference_lines) else -1
        self.line=self.reference_lines[index]['points'] if self.selected_line>=0 else None
        self.selectionChanged.emit(self.selected_line);self.viewport().update()

    def clear_lines(self):
        self.reference_lines=[];self.invalid_lines=set();self.line_drag=None
        self.select_line(-1)

    def mousePressEvent(self,event):
        if not self.line_mode or event.button()!=Qt.MouseButton.LeftButton or self.item.pixmap().isNull():
            return CropCanvas.mousePressEvent(self,event)
        rect=self.item.boundingRect();click=event.position().toPoint()
        # Search the selected line first when endpoints overlap.
        order=sorted(range(len(self.reference_lines)),key=lambda i:i!=self.selected_line)
        for i in order:
            points=self.reference_lines[i]['points']
            for j,p in enumerate(points):
                screen=self.mapFromScene(QPointF(p[0]*rect.width(),p[1]*rect.height()))
                if (screen-click).manhattanLength()<18:
                    self.select_line(i);self.line_drag=j;return
        for i in order:
            points=[self.mapFromScene(QPointF(p[0]*rect.width(),p[1]*rect.height())) for p in self.reference_lines[i]['points']]
            a,b=np.array([[p.x(),p.y()] for p in points],dtype=float);delta=b-a
            t=np.clip(np.dot(np.array([click.x(),click.y()])-a,delta)/max(1.,np.dot(delta,delta)),0,1)
            if np.linalg.norm(np.array([click.x(),click.y()])-(a+t*delta))<=7:
                self.select_line(i);self.line_drag=None;return
        point=self.point(event).tolist()
        self.reference_lines.append({'points':[point,point.copy()],'axis':self.new_axis})
        self.select_line(len(self.reference_lines)-1);self.line_drag=1
        self.lineChanged.emit(self.line)

    def drawForeground(self,painter,rect):
        CropCanvas.drawForeground(self,painter,rect)
        if not self.line_mode:return
        bounds=self.item.boundingRect();scale=max(.001,self.transform().m11())
        font=painter.font();font.setPixelSize(max(1,round(13/scale)));painter.setFont(font)
        for i,row in enumerate(self.reference_lines):
            points=[QPointF(p[0]*bounds.width(),p[1]*bounds.height()) for p in row['points']]
            color=QColor('#aaaaaa' if i in self.invalid_lines else ('#ffffff' if i==self.selected_line else '#bbbbbb'))
            outline=QPen(QColor('#111111'),4);outline.setCosmetic(True);painter.setPen(outline);painter.drawLine(*points)
            pen=QPen(color,2 if i==self.selected_line else 1.5);pen.setCosmetic(True)
            if i in self.invalid_lines:pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen);painter.drawLine(*points)
            edge=QPen(QColor('#111111'),1);edge.setCosmetic(True);painter.setPen(edge)
            painter.setBrush(color);radius=(5 if i==self.selected_line else 3)/scale
            for point in points:painter.drawEllipse(point,radius,radius)
            middle=(points[0]+points[1])/2
            label=f'{i+1} '+('H' if row['axis']=='horizontal' else 'V')+(' !' if i in self.invalid_lines else '')
            painter.drawText(middle+QPointF(7/scale,-5/scale),label)
            painter.setPen(color);painter.drawText(middle+QPointF(6/scale,-6/scale),label)


class PerspectiveCanvas(MultiLineCanvas):
    quadChanged=Signal(object)
    def __init__(self):
        super().__init__();self.perspective_mode=False;self.quad=[p.copy() for p in perspective.FULL]
        self.quad_drag=None;self.quad_invalid=False
    def mousePressEvent(self,event):
        if not self.perspective_mode:return super().mousePressEvent(event)
        if event.button()!=Qt.MouseButton.LeftButton:return
        rect=self.item.boundingRect()
        for i,p in enumerate(self.quad):
            screen=self.mapFromScene(QPointF(p[0]*rect.width(),p[1]*rect.height()))
            if (screen-event.position().toPoint()).manhattanLength()<22:self.quad_drag=i;return
    def mouseMoveEvent(self,event):
        if not self.perspective_mode:return super().mouseMoveEvent(event)
        if self.quad_drag is not None:
            self.quad[self.quad_drag]=self.point(event).tolist();self.quadChanged.emit(self.quad)
            self.viewport().update()
    def mouseReleaseEvent(self,event):
        if not self.perspective_mode:return super().mouseReleaseEvent(event)
        if event.button()==Qt.MouseButton.LeftButton and self.quad_drag is not None:
            self.quad[self.quad_drag]=self.point(event).tolist();self.quadChanged.emit(self.quad)
            self.quad_drag=None;self.viewport().update()
    def drawForeground(self,painter,rect):
        if not self.perspective_mode:return super().drawForeground(painter,rect)
        if self.item.pixmap().isNull():return
        bounds=self.item.boundingRect();scale=max(.001,self.transform().m11())
        points=[QPointF(x*bounds.width(),y*bounds.height()) for x,y in self.quad]
        path=QPainterPath();path.addRect(bounds);path.addPolygon(QPolygonF(points));path.setFillRule(Qt.FillRule.OddEvenFill)
        painter.fillPath(path,QColor(0,0,0,155));color=QColor('#aaaaaa' if self.quad_invalid else '#bbbbbb')
        pen=QPen(QColor('#111111'),4);pen.setCosmetic(True);painter.setPen(pen);painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPolygon(QPolygonF(points));pen.setColor(color);pen.setWidthF(1.5)
        if self.quad_invalid:pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawPolygon(QPolygonF(points))
        if not self.quad_invalid:
            inverse=perspective.matrix(self.quad)
            for i in range(1,self.grid_divisions):
                t=i/self.grid_divisions
                for ends in (((t,0),(t,1)),((0,t),(1,t))):
                    a,b=perspective.map_points(inverse,ends)*[bounds.width(),bounds.height()]
                    painter.drawLine(QPointF(*a),QPointF(*b))
        font=painter.font();font.setPixelSize(max(1,round(14/scale)));painter.setFont(font)
        painter.setBrush(color)
        for label,point in zip(('左上','右上','右下','左下'),points):
            painter.drawEllipse(point,6/scale,6/scale)
            dx=8/scale if point.x()<bounds.width()/2 else -40/scale
            dy=19/scale if point.y()<bounds.height()/2 else -10/scale
            painter.drawText(point+QPointF(dx,dy),label)


class GeometryDialog(QDialog):
    applied = Signal(object)

    def __init__(self,preview,value,parent=None):
        super().__init__(parent)
        self.setWindowTitle('裁切、校直与透视 · 无损编辑');self.setModal(True)
        self.resize(1100,850);self.setMinimumSize(850,650)
        self.preview = preview
        turns,angle,crop = geometry.settings(value)
        self.value = {'rotation':turns*90,'straighten':angle,'crop':crop}
        spec=perspective.normalize(value.get('perspective'))
        if spec is not None:self.value['perspective']=spec
        self.syncing = False
        layout = QVBoxLayout(self)
        intro = QLabel('拖动角点调整裁切，框内拖动移动，框外拖动重选。拉直自动收去空边；应用后可撤销，原图保持完整。')
        intro.setWordWrap(True);layout.addWidget(intro)
        split=QSplitter();self.canvas = PerspectiveCanvas();split.addWidget(self.canvas)
        if spec is not None:self.canvas.quad=[p.copy() for p in spec['points']]
        self.result_canvas=ImageCanvas();self.result_canvas.setAccessibleName('画线校直实时结果预览')
        split.addWidget(self.result_canvas);self.result_canvas.hide();split.setSizes([740,360]);layout.addWidget(split,1)
        self.timer = QTimer(self);self.timer.setSingleShot(True);self.timer.setInterval(80);self.timer.timeout.connect(self.refresh)
        row = QHBoxLayout()
        for text,step in (('左转 90°',-90),('右转 90°',90)):
            button = QPushButton(text);button.clicked.connect(lambda _,s=step:self.turn(s));row.addWidget(button)
        self.rotation_label = QLabel();row.addWidget(self.rotation_label)
        row.addStretch();row.addWidget(QLabel('裁切比例'))
        self.ratios = QComboBox();self.ratios.setAccessibleName('裁切比例')
        for title,ratio in [('自由',None),('原始比例','original'),('1 : 1',1.),('3 : 2',1.5),('2 : 3',2/3),
                            ('4 : 3',4/3),('3 : 4',.75),('16 : 9',16/9),('9 : 16',9/16)]:
            self.ratios.addItem(title,ratio)
        self.ratios.currentIndexChanged.connect(self.ratio_changed);row.addWidget(self.ratios)
        reset = QPushButton('重置几何');reset.clicked.connect(self.reset_geometry);row.addWidget(reset);layout.addLayout(row)
        self.angle = ParameterRow('拉直角度','straighten',-15,15,.05,angle)
        self.angle.spin.setSuffix('°');self.angle.changed.connect(self.angle_changed);layout.addWidget(self.angle)
        row=QHBoxLayout();self.line_button=QPushButton('画线校直');self.line_button.setCheckable(True)
        self.line_button.setAccessibleName('画线校直');self.line_button.toggled.connect(self.toggle_line);row.addWidget(self.line_button)
        self.line_axis=QComboBox();self.line_axis.setAccessibleName('校直参考方向')
        self.line_axis.addItem('设为水平','horizontal');self.line_axis.addItem('设为竖直','vertical')
        self.line_axis.currentIndexChanged.connect(self.line_axis_changed);row.addWidget(self.line_axis)
        self.grid=QComboBox();self.grid.setAccessibleName('校直辅助网格')
        for title,divisions in [('三分网格',3),('密集网格',10),('关闭网格',1)]:self.grid.addItem(title,divisions)
        self.grid.currentIndexChanged.connect(self.change_grid);row.addWidget(self.grid);row.addStretch();layout.addLayout(row)
        row=QHBoxLayout();row.addWidget(QLabel('参考线'))
        self.line_list=QComboBox();self.line_list.setAccessibleName('参考线列表');self.line_list.setMinimumWidth(180)
        self.line_list.currentIndexChanged.connect(self.canvas.select_line);row.addWidget(self.line_list,1)
        self.add_line_button=QPushButton('新增参考线');self.add_line_button.clicked.connect(self.add_line);row.addWidget(self.add_line_button)
        self.delete_line_button=QPushButton('删除选中线');self.delete_line_button.clicked.connect(self.delete_line);row.addWidget(self.delete_line_button)
        self.clear_lines_button=QPushButton('清空参考线');self.clear_lines_button.clicked.connect(self.clear_lines);row.addWidget(self.clear_lines_button)
        layout.addLayout(row);self.canvas.selectionChanged.connect(self.line_selected)
        self.line_message=QLabel('左侧空白处拖动可连续添加多条线；每条可设为水平或竖直，按原图线长加权计算角度。右侧实时预览。')
        self.line_message.setWordWrap(True);layout.addWidget(self.line_message)
        self.canvas.lineChanged.connect(self.line_changed)
        row=QHBoxLayout();self.perspective_button=QPushButton('四角透视校正');self.perspective_button.setCheckable(True)
        self.perspective_button.toggled.connect(self.toggle_perspective);row.addWidget(self.perspective_button)
        row.addWidget(QLabel('透视输出比例'));self.perspective_ratio=QComboBox();self.perspective_ratio.setAccessibleName('透视输出比例')
        for title,ratio in [('自动边长',None),('1 : 1',1.),('3 : 2',1.5),('2 : 3',2/3),('4 : 3',4/3),('3 : 4',.75),('16 : 9',16/9),('9 : 16',9/16)]:
            self.perspective_ratio.addItem(title,ratio)
        if spec is not None and spec['ratio'] is not None:
            index=self.perspective_ratio.findData(spec['ratio'])
            if index<0:self.perspective_ratio.addItem(f'自定 {spec["ratio"]:.4g} : 1',spec['ratio']);index=self.perspective_ratio.count()-1
            self.perspective_ratio.setCurrentIndex(index)
        self.perspective_ratio.currentIndexChanged.connect(lambda:self.quad_changed(self.canvas.quad))
        self.perspective_ratio.setEnabled(False)
        row.addWidget(self.perspective_ratio);self.clear_perspective_button=QPushButton('清除透视')
        self.clear_perspective_button.clicked.connect(self.clear_perspective);row.addWidget(self.clear_perspective_button);row.addStretch();layout.addLayout(row)
        self.perspective_message=QLabel('四角透视：在同一平面上拖动左上、右上、右下、左下四个角，右侧预览拉正结果。')
        self.perspective_message.setWordWrap(True);layout.addWidget(self.perspective_message)
        self.canvas.quadChanged.connect(self.quad_changed)
        row = QHBoxLayout();self.edges=[]
        for title,number in zip(('左 %','上 %','右 %','下 %'),crop):
            row.addWidget(QLabel(title));spin=QDoubleSpinBox();spin.setRange(0,100);spin.setDecimals(2)
            spin.setValue(number*100);spin.setKeyboardTracking(False);spin.setAccessibleName('裁切'+title)
            spin.editingFinished.connect(self.edges_changed);row.addWidget(spin);self.edges.append(spin)
        layout.addLayout(row)
        self.dimensions = QLabel();self.dimensions.setWordWrap(True);layout.addWidget(self.dimensions)
        row = QHBoxLayout();self.reset_crop_button=QPushButton('恢复完整画面');self.reset_crop_button.clicked.connect(self.reset_crop)
        row.addWidget(self.reset_crop_button);row.addStretch()
        cancel=QPushButton('取消');cancel.clicked.connect(self.reject);row.addWidget(cancel)
        self.apply_button=QPushButton('应用到当前帧');self.apply_button.setDefault(True);self.apply_button.clicked.connect(self.apply_result);row.addWidget(self.apply_button)
        layout.addLayout(row)
        self.canvas.cropChanged.connect(self.crop_changed)
        self.toggle_line(False)
        if spec is not None:self.perspective_button.setChecked(True)

    def turn(self,step):
        self.value['rotation']=(self.value['rotation']+step)%360
        self.value['crop']=geometry.FULL.copy()
        self.clear_perspective()
        self.clear_lines()
        self.refresh();self.ratio_changed()

    def angle_changed(self,value):
        self.value['straighten']=value
        if self.canvas.reference_lines:self.line_message.setText(f'手动角度 {value:+.2f}°；调整参考线后将重新综合计算。')
        self.timer.start()

    def refresh(self):
        self.timer.stop()
        value=dict(self.value,crop=geometry.FULL)
        from input_color import display_working
        working=self.preview.get('working_pixels',self.preview['pixels'].astype(np.float32)/255)
        if self.canvas.line_mode:
            reference=dict(value,straighten=0.,perspective=None)
            raw=geometry.apply(working,reference)
            reference_shown=display_working(raw,self.preview.get('working_space','srgb'),self.preview.get('monitor'))
            self.canvas.set_pixels(reference_shown);self.canvas.set_crop(geometry.FULL)
            final=geometry.apply(working,self.value)
            final_shown=display_working(final,self.preview.get('working_space','srgb'),self.preview.get('monitor'))
            self.result_canvas.set_pixels(final_shown)
        elif self.canvas.perspective_mode:
            reference=dict(value,perspective=None)
            raw=geometry.apply(working,reference)
            self.canvas.set_pixels(display_working(raw,self.preview.get('working_space','srgb'),self.preview.get('monitor')))
            self.canvas.set_crop(geometry.FULL)
            final=geometry.apply(working,self.value)
            self.result_canvas.set_pixels(display_working(final,self.preview.get('working_space','srgb'),self.preview.get('monitor')))
        else:
            out=geometry.apply(self.preview['pixels'].astype(np.float32)/255,value)
            wide=geometry.apply(working,value)
            shown=display_working(wide,self.preview.get('working_space','srgb'),self.preview.get('monitor'))
            self.canvas.set_pixels((out*255+.5).astype(np.uint8),display=shown)
            self.canvas.set_crop(self.value['crop'])
        self.rotation_label.setText(f'顺时针 {self.value["rotation"]}°')
        self.update_dimensions()

    def toggle_line(self,on):
        if on:self.perspective_button.setChecked(False)
        self.canvas.line_mode=on;self.canvas.drag_kind=None;self.canvas.line_drag=None
        self.result_canvas.setVisible(on or self.canvas.perspective_mode);self.refresh()
        for widget in (self.line_axis,self.line_list,self.add_line_button,self.delete_line_button,self.clear_lines_button):widget.setEnabled(on)
        self.update_line_list()

    def toggle_perspective(self,on):
        if on:self.line_button.setChecked(False)
        elif self.canvas.quad_invalid:
            self.canvas.quad=[p.copy() for p in self.value.get('perspective',{}).get('points',perspective.FULL)]
            self.canvas.quad_invalid=False;self.apply_button.setEnabled(True)
        self.canvas.perspective_mode=on;self.canvas.quad_drag=None;self.canvas.drag_kind=None
        self.perspective_ratio.setEnabled(on);self.result_canvas.setVisible(on or self.canvas.line_mode);self.refresh()

    def quad_changed(self,points):
        try:
            spec=perspective.normalize({'points':points,'ratio':self.perspective_ratio.currentData()})
            if spec is not None:perspective.matrix(spec['points'])
        except ValueError as error:
            self.canvas.quad_invalid=True;self.apply_button.setEnabled(False);self.timer.stop()
            self.perspective_message.setText(str(error)+'；请调整角点，右侧保留上次有效结果。');return
        self.canvas.quad_invalid=False;self.apply_button.setEnabled(True)
        if spec is None:self.value.pop('perspective',None)
        else:self.value['perspective']=spec
        self.perspective_message.setText('四角顺序：左上 → 右上 → 右下 → 左下。右侧为结果；可指定输出比例，或关闭四角模式继续裁切。')
        self.timer.start();self.canvas.viewport().update()

    def clear_perspective(self):
        self.value.pop('perspective',None);self.canvas.quad=[p.copy() for p in perspective.FULL];self.canvas.quad_invalid=False
        self.perspective_ratio.blockSignals(True);self.perspective_ratio.setCurrentIndex(0);self.perspective_ratio.blockSignals(False)
        self.apply_button.setEnabled(True);self.perspective_message.setText('透视已清除；其他裁切与校直保留。')
        self.refresh()

    def change_grid(self):
        self.canvas.grid_divisions=self.grid.currentData();self.canvas.viewport().update()

    def line_changed(self,points):
        if not points:return
        # Keep the direct single-reference adapter used by integrations.
        if self.canvas.selected_line<0:
            self.canvas.reference_lines.append({'points':points,'axis':self.line_axis.currentData()})
            self.canvas.select_line(len(self.canvas.reference_lines)-1)
        else:self.canvas.reference_lines[self.canvas.selected_line]['points']=points
        self.canvas.line=points
        self.recalculate_lines()

    def line_axis_changed(self):
        self.canvas.new_axis=self.line_axis.currentData()
        if self.canvas.selected_line>=0:
            self.canvas.reference_lines[self.canvas.selected_line]['axis']=self.canvas.new_axis
            self.recalculate_lines()

    def line_selected(self,index):
        self.line_axis.blockSignals(True)
        if index>=0:self.line_axis.setCurrentIndex(self.line_axis.findData(self.canvas.reference_lines[index]['axis']))
        self.line_axis.blockSignals(False);self.canvas.new_axis=self.line_axis.currentData()
        self.update_line_list()

    def update_line_list(self):
        self.line_list.blockSignals(True);self.line_list.clear()
        for i,row in enumerate(self.canvas.reference_lines):
            self.line_list.addItem(f'{i+1} · '+('水平' if row['axis']=='horizontal' else '竖直')+(' · 未采用' if i in self.canvas.invalid_lines else ''))
        self.line_list.setCurrentIndex(self.canvas.selected_line);self.line_list.blockSignals(False)
        self.delete_line_button.setEnabled(self.canvas.line_mode and self.canvas.selected_line>=0)
        self.clear_lines_button.setEnabled(self.canvas.line_mode and bool(self.canvas.reference_lines))

    def delete_line(self):
        index=self.canvas.selected_line
        if index<0:return
        del self.canvas.reference_lines[index]
        self.canvas.select_line(min(index,len(self.canvas.reference_lines)-1))
        if self.canvas.reference_lines:self.recalculate_lines()
        else:self.clear_lines()

    def add_line(self):
        self.canvas.line_drag=None;self.canvas.select_line(-1)
        self.line_message.setText('请选择新线的水平 / 竖直方向，再在左侧空白处拖动画线。已有参考线继续参与计算。')

    def clear_lines(self):
        self.canvas.clear_lines();self.update_line_list()
        self.line_message.setText('参考线已清空，保留当前角度；可重新画线，或用“重置几何”归零。')

    def recalculate_lines(self):
        shape=self.preview['source_shape']
        if self.value['rotation']%180:shape=(shape[1],shape[0])
        rows=self.canvas.reference_lines
        self.canvas.invalid_lines=set()
        for i,row in enumerate(rows):
            try:geometry.line_straighten(row['points'],shape,row['axis'])
            except ValueError:self.canvas.invalid_lines.add(i)
        self.update_line_list();self.canvas.viewport().update()
        try:result=geometry.lines_straighten(rows,shape)
        except ValueError as error:self.line_message.setText(str(error)+'；保留当前角度。');return
        angle=result['angle']
        message=f'{len(result["used"])} 条有效参考线 · 综合校直 {angle:+.2f}° · 最大角度偏差 {result["max_deviation"]:.2f}°。'
        if result['rejected']:
            message+=' 未采用：'+'；'.join(f'{r["index"]+1} 号（{r["reason"]}）' for r in result['rejected'])
        self.line_message.setText(message)
        self.value['straighten']=angle;self.angle.set_value(angle);self.timer.start()

    def crop_changed(self,crop):
        self.value['crop']=geometry.normalize_crop(crop)
        self.update_dimensions()

    def update_dimensions(self):
        self.syncing=True
        for spin,value in zip(self.edges,self.value['crop']):
            spin.setValue(value*100)
        self.syncing=False
        h,w=geometry.output_shape(self.preview['source_shape'],self.value)
        oh,ow=self.preview['source_shape']
        self.dimensions.setText(f'原始帧 {ow} × {oh}  →  全尺寸输出 {w} × {h} px   ·   拉直范围 ±15°；比例不改变输出像素密度')

    def edges_changed(self):
        if self.syncing:return
        # Numeric edges are explicit; selecting a preset enables ratio locking again.
        crop=geometry.normalize_crop([spin.value()/100 for spin in self.edges])
        self.ratios.blockSignals(True);self.ratios.setCurrentIndex(0);self.ratios.blockSignals(False)
        self.canvas.ratio=None;self.canvas.set_crop(crop);self.crop_changed(crop)

    def ratio_changed(self,*_):
        ratio=self.ratios.currentData()
        if ratio=='original':
            h,w=self.preview['source_shape']
            ratio=(h/w if self.value['rotation']%180 else w/h)
        self.canvas.ratio=ratio
        if not ratio:return
        h,w=geometry.output_shape(self.preview['source_shape'],dict(self.value,crop=geometry.FULL))
        target=ratio*h/w
        x0,y0,x1,y1=self.value['crop'];cx,cy=(x0+x1)/2,(y0+y1)/2
        width,height=x1-x0,y1-y0
        if width/height>target:width=height*target
        else:height=width/target
        crop=[cx-width/2,cy-height/2,cx+width/2,cy+height/2]
        self.canvas.set_crop(crop);self.crop_changed(crop)

    def reset_crop(self):
        self.value['crop']=geometry.FULL.copy()
        self.ratios.setCurrentIndex(0);self.canvas.ratio=None
        self.canvas.set_crop(self.value['crop']);self.update_dimensions()

    def reset_geometry(self):
        self.value={'rotation':0,'straighten':0.,'crop':geometry.FULL.copy()}
        self.clear_perspective();self.perspective_button.setChecked(False)
        self.clear_lines()
        self.angle.set_value(0);self.ratios.setCurrentIndex(0);self.canvas.ratio=None
        self.refresh()

    def apply_result(self):
        if self.canvas.quad_invalid:return
        self.refresh()
        self.applied.emit(dict(self.value));self.accept()
