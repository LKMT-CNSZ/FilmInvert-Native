"""Source-coordinate frame editor, independent of per-frame output cropping."""
import numpy as np
import copy
import uuid
from PySide6.QtCore import Qt, Signal, QRectF, QPointF
from PySide6.QtGui import QColor,QPen,QAction,QKeySequence
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QComboBox,
    QListWidget,QSpinBox,QDoubleSpinBox,QSplitter,QWidget,QGridLayout,QScrollArea)
import strip
from native_geometry import MultiLineCanvas
import geometry
from strip_alignment import same_strip


class FrameCanvas(MultiLineCanvas):
    editFinished=Signal()
    def __init__(self):
        super().__init__();self.boxes=[];self.selected=0;self.editable=True;self.edge_lines=[];self.polygons=[]
        self.setAccessibleName('切帧概览：选中一帧后拖动角点或框内移动')
    def mousePressEvent(self,event):
        if self.editable:super().mousePressEvent(event)
    def mouseMoveEvent(self,event):
        if self.editable:super().mouseMoveEvent(event)
    def mouseReleaseEvent(self,event):
        editing=self.drag_kind is not None or self.line_drag is not None
        super().mouseReleaseEvent(event)
        if editing:self.editFinished.emit()
    def drawForeground(self,painter,rect):
        super().drawForeground(painter,rect)
        image=self.item.boundingRect();scale=max(.001,self.transform().m11())
        pen=QPen(QColor('#999999'),1.5);pen.setCosmetic(True);painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        font=painter.font();font.setPixelSize(max(1,round(14/scale)));painter.setFont(font)
        for i,box in enumerate(self.boxes):
            x0,y0,x1,y1=box;area=QRectF(x0*image.width(),y0*image.height(),(x1-x0)*image.width(),(y1-y0)*image.height())
            if i!=self.selected:painter.drawRect(area)
            painter.drawText(QPointF(area.left()+5/scale,area.top()+18/scale),str(i+1))
        pen.setColor(QColor('#bbbbbb'));painter.setPen(pen)
        for a,b in self.edge_lines:painter.drawLine(QPointF(a[0]*image.width(),a[1]*image.height()),QPointF(b[0]*image.width(),b[1]*image.height()))
        from PySide6.QtGui import QPolygonF
        pen.setColor(QColor('#ededed'));painter.setPen(pen)
        for points in self.polygons:painter.drawPolygon(QPolygonF([QPointF(x*image.width(),y*image.height()) for x,y in points]))


class FrameLayoutDialog(QDialog):
    applied=Signal(object)
    def __init__(self,preview,parent=None):
        super().__init__(parent);self.setWindowTitle('输入与切帧 · 原始扫描概览');self.setModal(True)
        self.resize(1210,850);self.setMinimumSize(920,650)
        self.preview=preview;self.width_=preview['width'];self.height_=preview['height']
        self.rows=copy.deepcopy(preview['frames']);self.manual_rows=copy.deepcopy(self.rows)
        self.syncing=True;self.history=[];self.history_index=-1
        self.reference_sets={};self.reference_frame=None
        layout=QVBoxLayout(self)
        intro=QLabel('选框标在原始扫描上，编号就是导出顺序。手动模式下选中一帧，拖动角点 / 框内移动，或输入原图像素边界。')
        intro.setWordWrap(True);layout.addWidget(intro)
        row=QHBoxLayout();row.addWidget(QLabel('处理方式'))
        self.mode=QComboBox();self.mode.setAccessibleName('当前图像处理方式')
        for title,key in [('自动识别','auto'),('单张完整图像','single'),('整条自动切帧','strip'),('手动切帧','manual')]:self.mode.addItem(title,key)
        initial='manual' if preview['input']['manual'] else preview['input']['mode']
        self._mode_state=initial
        self.mode.setCurrentIndex(self.mode.findData(initial));row.addWidget(self.mode)
        row.addWidget(QLabel('切出后顺时针旋转'))
        self.rotation=QComboBox();self.rotation.setAccessibleName('切出后的旋转方向')
        for angle in (0,90,180,270):self.rotation.addItem(f'{angle}°',angle)
        self.rotation.setCurrentIndex(self.rotation.findData(preview['rotation']));row.addWidget(self.rotation)
        row.addStretch();self.undo=QPushButton('撤销编辑');self.undo.clicked.connect(lambda:self.travel(-1));row.addWidget(self.undo)
        self.redo=QPushButton('重做');self.redo.clicked.connect(lambda:self.travel(1));row.addWidget(self.redo);layout.addLayout(row)
        split=QSplitter();layout.addWidget(split,1)
        from input_color import display_working
        shown=display_working(preview.get('working_pixels',preview['pixels'].astype(np.float32)/255),preview.get('working_space','srgb'),preview.get('monitor'))
        self.canvas=FrameCanvas();self.canvas.set_pixels(preview['pixels'],display=shown);split.addWidget(self.canvas)
        side=QWidget();side.setMinimumWidth(342);side.setMaximumWidth(410);side_layout=QVBoxLayout(side)
        self.frames=QListWidget();self.frames.setAccessibleName('切帧选框列表');self.frames.setMinimumHeight(70);self.frames.setMaximumHeight(160)
        side_layout.addWidget(self.frames,1)
        body=QWidget();panel=QVBoxLayout(body);panel.setContentsMargins(0,0,4,0)
        self.controls_scroll=QScrollArea();self.controls_scroll.setWidgetResizable(True);self.controls_scroll.setWidget(body)
        side_layout.addWidget(self.controls_scroll,4)
        controls=QGridLayout();self.edit_buttons=[]
        for i,(title,fn) in enumerate([('添加选框',self.add_frame),('移除选框',self.remove_frame),('上移',lambda:self.move_frame(-1)),('下移',lambda:self.move_frame(1))]):
            button=QPushButton(title);button.clicked.connect(fn);controls.addWidget(button,i//2,i%2);self.edit_buttons.append(button)
        panel.addLayout(controls)
        grid=QGridLayout();self.edges=[]
        for i,title in enumerate(('左','上','右','下')):
            spin=QSpinBox();spin.setRange(0,self.width_ if i%2==0 else self.height_);spin.setKeyboardTracking(False)
            spin.setAccessibleName('切帧'+title+'边界像素');spin.editingFinished.connect(self.edges_changed)
            grid.addWidget(QLabel(title+' px'),i//2,(i%2)*2);grid.addWidget(spin,i//2,(i%2)*2+1);self.edges.append(spin)
        panel.addLayout(grid)
        self.size_label=QLabel();panel.addWidget(self.size_label)
        panel.addWidget(QLabel('扫描片框校直（切帧前）'))
        self.input_angle=QDoubleSpinBox();self.input_angle.setRange(-15,15);self.input_angle.setDecimals(3);self.input_angle.setSingleStep(.05)
        self.input_angle.setSuffix('°');self.input_angle.setAccessibleName('输入片框校直角度')
        self.input_angle.editingFinished.connect(lambda:self.set_input_angle(self.input_angle.value()));panel.addWidget(self.input_angle)
        self.analyze_button=QPushButton('查看片边倾角建议');self.analyze_button.clicked.connect(self.show_suggestion);panel.addWidget(self.analyze_button)
        self.align_note=QLabel('自动建议只依据物理片边；证据不足时手动画线。');self.align_note.setWordWrap(True);panel.addWidget(self.align_note)
        row=QHBoxLayout();self.adopt_button=QPushButton('采用到当前帧');self.adopt_button.clicked.connect(lambda:self.adopt_suggestion(False));row.addWidget(self.adopt_button)
        self.strip_button=QPushButton('采用到同条');self.strip_button.clicked.connect(lambda:self.adopt_suggestion(True));row.addWidget(self.strip_button);panel.addLayout(row)
        row=QHBoxLayout();self.line_button=QPushButton('沿片边画线');self.line_button.setCheckable(True);self.line_button.toggled.connect(self.line_mode);row.addWidget(self.line_button)
        self.line_axis=QComboBox();self.line_axis.addItem('竖直片边','vertical');self.line_axis.addItem('水平片边','horizontal')
        self.line_axis.setAccessibleName('片边参考线方向');self.line_axis.currentIndexChanged.connect(self.line_axis_changed)
        row.addWidget(self.line_axis);panel.addLayout(row)
        self.line_list=QComboBox();self.line_list.setAccessibleName('片边参考线列表')
        self.line_list.currentIndexChanged.connect(self.canvas.select_line);panel.addWidget(self.line_list)
        row=QHBoxLayout()
        self.add_line_button=QPushButton('新增参考线');self.add_line_button.clicked.connect(self.add_line);row.addWidget(self.add_line_button)
        self.delete_line_button=QPushButton('删除选中线');self.delete_line_button.clicked.connect(self.delete_line);row.addWidget(self.delete_line_button)
        self.clear_lines_button=QPushButton('清空');self.clear_lines_button.clicked.connect(lambda:self.clear_lines());row.addWidget(self.clear_lines_button);panel.addLayout(row)
        self.line_help=QLabel('空白处拖动可连续加线，点击线段选中、拖动端点调整。参考线按帧独立；可将综合角度采用到同条。')
        self.line_help.setWordWrap(True);panel.addWidget(self.line_help)
        self.canvas.lineChanged.connect(self.input_line_changed)
        self.canvas.selectionChanged.connect(self.line_selected)
        self.candidate=None
        note=QLabel('等分当前选框（替换为网格）');panel.addWidget(note)
        row=QHBoxLayout();self.grid_rows=QSpinBox();self.grid_rows.setRange(1,50);self.grid_rows.setValue(1)
        self.grid_rows.setAccessibleName('等分行数');self.grid_cols=QSpinBox();self.grid_cols.setRange(1,50);self.grid_cols.setValue(2)
        self.grid_cols.setAccessibleName('等分列数');self.gap=QSpinBox();self.gap.setRange(0,512);self.gap.setAccessibleName('等分间隙像素')
        row.addWidget(QLabel('行'));row.addWidget(self.grid_rows);row.addWidget(QLabel('列'));row.addWidget(self.grid_cols)
        row.addWidget(QLabel('间隙'));row.addWidget(self.gap);panel.addLayout(row)
        self.divide_button=QPushButton('等分选框');self.divide_button.clicked.connect(self.divide);panel.addWidget(self.divide_button)
        split.addWidget(side);split.setSizes([850,330])
        self.message=QLabel();self.message.setWordWrap(True);layout.addWidget(self.message)
        note=QLabel('应用后保留匹配帧的调色；边界或切出方向改变的帧会清除旧裁切、校直和照明校正。手动布局不估计帧间隙平场；新增帧的片基需检查。可在主窗口恢复切帧前状态。')
        note.setWordWrap(True);layout.addWidget(note)
        row=QHBoxLayout();row.addStretch();cancel=QPushButton('取消');cancel.clicked.connect(self.reject);row.addWidget(cancel)
        apply=QPushButton('应用输入与切帧');apply.setProperty('role','primary');apply.clicked.connect(self.apply_result);row.addWidget(apply);layout.addLayout(row)
        self.frames.currentRowChanged.connect(self.select)
        self.canvas.cropChanged.connect(self.crop_changed);self.canvas.editFinished.connect(self.commit)
        self.mode.currentIndexChanged.connect(self.mode_changed);self.rotation.currentIndexChanged.connect(self.rotation_changed)
        for key,direction in [('Ctrl+Z',-1),('Ctrl+Shift+Z',1)]:
            action=QAction(self);action.setShortcut(QKeySequence(key));action.triggered.connect(lambda _,d=direction:self.travel(d));self.addAction(action)
        self.syncing=False;self.refresh(0);self.line_mode(False);self.commit()

    def manual(self):return self.mode.currentData()=='manual'
    def make_manual(self):
        if not self.manual():
            self.mode.blockSignals(True);self.mode.setCurrentIndex(self.mode.findData('manual'));self.mode.blockSignals(False)
            self._mode_state='manual'
            self.refresh(self.frames.currentRow())
    def mode_changed(self,*_):
        if self.syncing:return
        mode=self.mode.currentData()
        if self._mode_state=='manual':self.manual_rows=copy.deepcopy(self.rows)
        self._mode_state=mode
        if mode=='manual':self.rows=copy.deepcopy(self.manual_rows)
        else:
            if mode=='single':
                self.rows=[{'id':'frame-0','box':[0,0,self.width_,self.height_]}];rotation=0
            else:self.rows=copy.deepcopy(self.preview['detected_frames']);rotation=self.preview['detected_rotation']
            self.rotation.blockSignals(True);self.rotation.setCurrentIndex(self.rotation.findData(rotation));self.rotation.blockSignals(False)
        self.refresh(0);self.commit()
    def rotation_changed(self,*_):
        if self.syncing:return
        self.make_manual();self.commit();self.refresh(self.frames.currentRow())
    def refresh(self,index=0):
        self.syncing=True;self.frames.blockSignals(True);self.frames.clear()
        for i,row in enumerate(self.rows):
            x0,y0,x1,y1=row['box'];h,w=geometry.output_shape((y1-y0,x1-x0),{'straighten':row.get('angle',0.)})
            if self.rotation.currentData()%180:w,h=h,w
            self.frames.addItem(f'{i+1:02d}  ·  {w} × {h} px\n({x0}, {y0}) – ({x1}, {y1})')
        index=max(0,min(index,len(self.rows)-1));self.frames.setCurrentRow(index);self.frames.blockSignals(False)
        for button in [*self.edit_buttons,self.divide_button,*self.edges,self.grid_rows,self.grid_cols,self.gap]:button.setEnabled(self.manual())
        self.rotation.setEnabled(self.manual());self.canvas.editable=self.manual()
        if not self.manual() and self.line_button.isChecked():self.line_button.setChecked(False)
        self.canvas.boxes=[[x0/self.width_,y0/self.height_,x1/self.width_,y1/self.height_] for x0,y0,x1,y1 in [row['box'] for row in self.rows]]
        self.canvas.polygons=[]
        for row in self.rows:
            if not row.get('angle'):continue
            x0,y0,x1,y1=row['box'];shape=(y1-y0,x1-x0)
            points=[geometry.source_point(x,y,shape,{'straighten':row['angle']}) for x,y in ((0,0),(1,0),(1,1),(0,1))]
            self.canvas.polygons.append([((x0+x*(x1-x0))/self.width_,(y0+y*(y1-y0))/self.height_) for x,y in points])
        self.syncing=False;self.select(index)
        self.message.setText(f'原始扫描 {self.width_} × {self.height_} px · {len(self.rows)} 帧 · '+('手动选框可编辑；框之间允许重叠。' if self.manual() else '切换到“手动切帧”可编辑选框；自动 / 单张结果将在应用时生效。'))
    def select(self,index):
        if self.syncing or not 0<=index<len(self.rows):return
        self.syncing=True;self.canvas.selected=index
        frame_id=self.rows[index]['id'];changed=frame_id!=self.reference_frame
        box=self.rows[index]['box']
        for spin,value in zip(self.edges,box):spin.setValue(value)
        self.canvas.set_crop([box[0]/self.width_,box[1]/self.height_,box[2]/self.width_,box[3]/self.height_])
        self.size_label.setText(f'选中帧范围 {box[2]-box[0]} × {box[3]-box[1]} px')
        self.input_angle.setValue(self.rows[index].get('angle',0.))
        if changed:
            self.reference_frame=frame_id
            state=self.reference_sets.setdefault(frame_id,{'lines':[],'selected':-1})
            self.canvas.reference_lines=state['lines'];self.canvas.invalid_lines=set();self.canvas.line_drag=None
            self.canvas.select_line(state['selected']);self.candidate=None
            self.canvas.edge_lines=[];self.adopt_button.setEnabled(False);self.strip_button.setEnabled(False)
        self.syncing=False
        if changed:
            if self.canvas.reference_lines:self.recalculate_input_lines(False)
            else:self.align_note.setText('本帧尚无手动参考线；可查看自动建议，或沿片边连续画线。')
        self.update_line_controls()

    def set_input_angle(self,angle,commit=True):
        if self.syncing:return
        index=self.frames.currentRow();self.make_manual()
        if angle:self.rows[index]['angle']=float(angle)
        else:self.rows[index].pop('angle',None)
        self.refresh(index)
        if commit:
            self.candidate=None;self.adopt_button.setEnabled(False);self.strip_button.setEnabled(False)
            self.align_note.setText(f'输入角度 {angle:+.3f}°；调整参考线后会重新综合计算。');self.commit()

    def line_mode(self,on):
        if on:self.make_manual()
        self.canvas.line_mode=on;self.canvas.line_drag=None;self.canvas.drag_kind=None;self.canvas.viewport().update()
        self.canvas.new_axis=self.line_axis.currentData();self.update_line_controls()

    def input_line_changed(self,points):
        if not points:return
        if self.canvas.selected_line<0:
            self.canvas.reference_lines.append({'points':points,'axis':self.line_axis.currentData()})
            self.canvas.select_line(len(self.canvas.reference_lines)-1)
        else:self.canvas.reference_lines[self.canvas.selected_line]['points']=points
        self.canvas.line=points;self.remember_lines();self.recalculate_input_lines()

    def remember_lines(self):
        if self.reference_frame is not None:
            self.reference_sets[self.reference_frame]={'lines':self.canvas.reference_lines,'selected':self.canvas.selected_line}

    def line_selected(self,index):
        self.line_axis.blockSignals(True)
        if index>=0:self.line_axis.setCurrentIndex(self.line_axis.findData(self.canvas.reference_lines[index]['axis']))
        self.line_axis.blockSignals(False);self.canvas.new_axis=self.line_axis.currentData()
        self.remember_lines();self.update_line_controls()

    def update_line_controls(self):
        self.line_list.blockSignals(True);self.line_list.clear()
        for i,row in enumerate(self.canvas.reference_lines):
            self.line_list.addItem(f'{i+1} · '+('竖直片边' if row['axis']=='vertical' else '水平片边')+(' · 未采用' if i in self.canvas.invalid_lines else ''))
        self.line_list.setCurrentIndex(self.canvas.selected_line);self.line_list.blockSignals(False)
        on=self.canvas.line_mode
        self.line_axis.setEnabled(on);self.line_list.setEnabled(on);self.add_line_button.setEnabled(on)
        self.delete_line_button.setEnabled(on and self.canvas.selected_line>=0)
        self.clear_lines_button.setEnabled(on and bool(self.canvas.reference_lines))

    def line_axis_changed(self):
        self.canvas.new_axis=self.line_axis.currentData()
        if self.canvas.selected_line>=0:
            self.canvas.reference_lines[self.canvas.selected_line]['axis']=self.canvas.new_axis
            self.recalculate_input_lines();self.commit()

    def add_line(self):
        self.canvas.line_drag=None;self.canvas.select_line(-1)
        self.align_note.setText('选择新线的水平 / 竖直方向，再在左侧空白处拖动；已有线继续参与本帧计算。')

    def delete_line(self):
        index=self.canvas.selected_line
        if index<0:return
        del self.canvas.reference_lines[index]
        self.canvas.select_line(min(index,len(self.canvas.reference_lines)-1))
        if self.canvas.reference_lines:self.recalculate_input_lines()
        else:self.clear_lines(False)
        self.commit()

    def clear_lines(self,commit=True):
        self.canvas.clear_lines();self.remember_lines();self.candidate=None
        self.adopt_button.setEnabled(False);self.strip_button.setEnabled(False)
        self.align_note.setText('本帧参考线已清空，保留当前输入角度。输入 0°可归零。')
        self.update_line_controls()
        if commit:self.commit()

    def recalculate_input_lines(self,apply=True):
        rows=self.canvas.reference_lines;shape=(self.height_,self.width_)
        self.canvas.invalid_lines=set()
        for i,row in enumerate(rows):
            try:geometry.line_straighten(row['points'],shape,row['axis'])
            except ValueError:self.canvas.invalid_lines.add(i)
        self.update_line_controls();self.canvas.viewport().update()
        try:result=geometry.lines_straighten(rows,shape)
        except ValueError as error:
            self.candidate=None;self.adopt_button.setEnabled(False);self.strip_button.setEnabled(False)
            self.align_note.setText(str(error)+'；保留当前输入角度。');return
        angle=result['angle']
        if apply:self.set_input_angle(angle,False)
        self.candidate={'source':'manual','available':True,'angle':angle}
        self.canvas.edge_lines=[];self.adopt_button.setEnabled(True);self.strip_button.setEnabled(True)
        text=f'{len(result["used"])} 条有效片边线 · 综合 {angle:+.3f}° · 最大偏差 {result["max_deviation"]:.2f}°。'
        text+=('已设置当前帧，应用切帧后生效。' if apply else '可采用此综合角度到当前帧或同条。')
        if result['rejected']:text+=' 未采用：'+'；'.join(f'{r["index"]+1} 号（{r["reason"]}）' for r in result['rejected'])
        self.align_note.setText(text)

    def show_suggestion(self):
        index=self.frames.currentRow();row=self.rows[index]
        candidate=next((c for c in self.preview.get('alignment_candidates',[]) if c['box']==row['box']),None)
        if candidate is None:
            self.candidate=None;self.adopt_button.setEnabled(False);self.strip_button.setEnabled(False)
            self.align_note.setText('请先应用新选框，再重新打开以分析片边；也可手动画线。');return
        self.candidate=candidate
        self.align_note.setText((f"建议 {candidate['angle']:+.3f}° · 可信度{candidate['confidence']} · " if candidate['available'] else '')+candidate['reason'])
        self.adopt_button.setEnabled(candidate['available']);self.strip_button.setEnabled(candidate['available'])
        self.canvas.edge_lines=[[[p[0]/self.width_,p[1]/self.height_] for p in edge['line']] for edge in candidate.get('edges',[])]
        self.canvas.viewport().update()

    def adopt_suggestion(self,whole_strip):
        if not self.candidate or not self.candidate['available']:return
        index=self.frames.currentRow();candidate=self.candidate;box=list(self.rows[index]['box']);self.make_manual();count=0
        for i,row in enumerate(self.rows):
            if i==index or (whole_strip and same_strip(box,row['box'])):
                row['angle']=candidate['angle'];count+=1
        self.refresh(index);self.commit();self.align_note.setText(f'已设置 {count} 帧输入校直 {candidate["angle"]:+.3f}°；可撤销，应用后生效。')
    def crop_changed(self,crop):
        if self.syncing or not self.manual():return
        i=self.frames.currentRow()
        box=[round(crop[0]*self.width_),round(crop[1]*self.height_),round(crop[2]*self.width_),round(crop[3]*self.height_)]
        if box[2]-box[0]<2 or box[3]-box[1]<2:return
        self.rows[i]['box']=box
        self.canvas.boxes[i]=[box[0]/self.width_,box[1]/self.height_,box[2]/self.width_,box[3]/self.height_]
        self.syncing=True
        for spin,value in zip(self.edges,box):spin.setValue(value)
        self.syncing=False
        self.size_label.setText(f'选中帧范围 {box[2]-box[0]} × {box[3]-box[1]} px')
        self.frames.item(i).setText(f'{i+1:02d}  ·  {box[2]-box[0]} × {box[3]-box[1]} px\n({box[0]}, {box[1]}) – ({box[2]}, {box[3]})')
    def edges_changed(self):
        if self.syncing or not self.manual():return
        i=self.frames.currentRow();rows=copy.deepcopy(self.rows);rows[i]['box']=[spin.value() for spin in self.edges]
        try:strip.normalize_manual({'rotation':self.rotation.currentData(),'frames':rows},(self.height_,self.width_))
        except ValueError as error:self.message.setText(str(error));self.select(i);return
        self.rows=rows;self.refresh(i);self.commit()
    def add_frame(self):
        self.make_manual()
        if len(self.rows)>=500:self.message.setText('最多支持 500 个手动选框');return
        index=self.frames.currentRow();row=copy.deepcopy(self.rows[index]);row['id']=uuid.uuid4().hex
        self.rows.insert(index+1,row);self.refresh(index+1);self.commit()
    def remove_frame(self):
        if len(self.rows)<=1:self.message.setText('至少保留一个选框；要处理整张请选择单张完整图像。');return
        i=self.frames.currentRow();self.rows.pop(i);self.refresh(i);self.commit()
    def move_frame(self,direction):
        i=self.frames.currentRow();j=i+direction
        if 0<=j<len(self.rows):self.rows[i],self.rows[j]=self.rows[j],self.rows[i];self.refresh(j);self.commit()
    def divide(self):
        i=self.frames.currentRow();x0,y0,x1,y1=self.rows[i]['box'];nr,nc,gap=self.grid_rows.value(),self.grid_cols.value(),self.gap.value()
        width,height=(x1-x0-gap*(nc-1))/nc,(y1-y0-gap*(nr-1))/nr
        if width<2 or height<2 or len(self.rows)-1+nr*nc>500:
            self.message.setText('网格过密或间隙过大；每格至少 2 × 2 像素，总帧数最多 500。');return
        cells=[]
        for r in range(nr):
            for c in range(nc):
                box=[round(x0+c*(width+gap)),round(y0+r*(height+gap)),round(x0+c*(width+gap)+width),round(y0+r*(height+gap)+height)]
                cells.append({'id':self.rows[i]['id'] if not cells else uuid.uuid4().hex,'box':box})
        self.rows[i:i+1]=cells;self.refresh(i);self.commit()
    def snapshot(self):
        return {'mode':self.mode.currentData(),'frames':copy.deepcopy(self.rows),'rotation':self.rotation.currentData(),
                'references':copy.deepcopy(self.reference_sets),'selected_frame':self.rows[self.frames.currentRow()]['id']}
    def commit(self):
        if self.syncing:return
        state=self.snapshot()
        if self.history_index>=0 and self.history[self.history_index]==state:return
        self.history=self.history[:self.history_index+1]+[state]
        if len(self.history)>51:self.history.pop(0)
        self.history_index=len(self.history)-1
        if self.manual():self.manual_rows=copy.deepcopy(self.rows)
        self.undo.setEnabled(self.history_index>0);self.redo.setEnabled(False)
    def travel(self,direction):
        target=self.history_index+direction
        if not 0<=target<len(self.history):return
        self.history_index=target;state=self.history[target];self.rows=copy.deepcopy(state['frames'])
        self.reference_sets=copy.deepcopy(state['references']);self.reference_frame=None
        self.mode.blockSignals(True);self.mode.setCurrentIndex(self.mode.findData(state['mode']));self.mode.blockSignals(False)
        self._mode_state=state['mode']
        self.rotation.blockSignals(True);self.rotation.setCurrentIndex(self.rotation.findData(state['rotation']));self.rotation.blockSignals(False)
        if self.manual():self.manual_rows=copy.deepcopy(self.rows)
        index=next((i for i,row in enumerate(self.rows) if row['id']==state['selected_frame']),0)
        self.refresh(index);self.undo.setEnabled(target>0);self.redo.setEnabled(target<len(self.history)-1)
    def apply_result(self):
        mode=self.mode.currentData()
        try:
            manual=strip.normalize_manual({'rotation':self.rotation.currentData(),'frames':self.rows},(self.height_,self.width_)) if mode=='manual' else None
        except ValueError as error:self.message.setText(str(error));return
        self.applied.emit({'mode':'strip' if mode=='manual' else mode,'manual':manual});self.accept()
