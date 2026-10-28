"""Native chart sampling and comparison dialog; computations run in the host worker."""
from i18n import tr as _t, trf as _tf, ui as _ui
from pathlib import Path
import copy
import json
import numpy as np
from PySide6.QtCore import Qt,Signal
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QFileDialog,
    QTableWidget,QTableWidgetItem,QAbstractItemView,QSpinBox,QCheckBox,QSplitter,QWidget)
import calibration
from native_widgets import ImageCanvas


class CalibrationDialog(QDialog):
    fitRequested=Signal(object,object,str)
    applied=Signal(object)
    def __init__(self,preview,profile,samples_path,parent=None):
        super().__init__(parent);_ui(self.setWindowTitle, _t('色卡标定 · 线性 RGB 校正'));self.setModal(True);self.resize(1200,820)
        self.preview=preview;self.linear=preview['linear'];self.samples_path=Path(samples_path)
        self.reference=[];self.points=[];self.name=_t('自定义色卡');self.result=None;self.busy=False;self.grid=[];self.grid_mode=False
        outer=QVBoxLayout(self)
        intro=_ui(QLabel, _t('导入参考 CSV → 取样 → 指定训练 / 验证色块 → 拟合并验证。\nRGB RMSE 为编码 sRGB 的 0–255 误差；ΔE00 由 sRGB 参考推算，不构成设备色准认证。'))
        intro.setWordWrap(True);outer.addWidget(intro)
        split=QSplitter();outer.addWidget(split,1)
        self.canvas=ImageCanvas();self.canvas.setMinimumWidth(520);self.canvas.setAcceptDrops(False)
        self.canvas.set_sampling(True);self.canvas.sampled.connect(self.sample_at);split.addWidget(self.canvas)
        side=QWidget();side.setMinimumWidth(500);layout=QVBoxLayout(side);split.addWidget(side)
        split.setSizes([640,520])
        row=QHBoxLayout();self.import_button=_ui(QPushButton, _t('导入参考 CSV'));self.demo_button=_ui(QPushButton, _t('演示参考'))
        self.import_button.clicked.connect(self.import_reference);self.demo_button.clicked.connect(lambda:self.load_reference(self.samples_path/'calibration-demo-reference.csv'))
        row.addWidget(self.import_button);row.addWidget(self.demo_button);layout.addLayout(row)
        self.reference_label=_ui(QLabel, _t('CSV：name,r,g,b；可加 role=train 或 validation；8–96 色，sRGB 0–255。'));self.reference_label.setWordWrap(True);layout.addWidget(self.reference_label)
        self.table=QTableWidget(0,4);_ui(self.table.setHorizontalHeaderLabels, [_t('色块'),_t('参考 RGB'),_t('取样位置'),_t('拟合前 → 后')])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows);self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers);self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0,95);self.table.setColumnWidth(1,105);self.table.setColumnWidth(2,105);layout.addWidget(self.table,1)
        self.validation_patch=_ui(QCheckBox, _t('选中色块仅用于独立验证（不参与拟合）'))
        self.validation_patch.toggled.connect(self.change_role);layout.addWidget(self.validation_patch)
        self.table.currentCellChanged.connect(self.sync_role)
        row=QHBoxLayout();self.rows=QSpinBox();self.rows.setRange(1,12);self.rows.setValue(4);_ui(self.rows.setAccessibleName, _t('色卡行数'))
        self.cols=QSpinBox();self.cols.setRange(1,12);self.cols.setValue(6);_ui(self.cols.setAccessibleName, _t('色卡列数'))
        row.addWidget(_ui(QLabel, _t('行')));row.addWidget(self.rows);row.addWidget(_ui(QLabel, _t('列')));row.addWidget(self.cols)
        self.grid_button=_ui(QPushButton, _t('两点框选网格'));self.grid_button.clicked.connect(self.start_grid);row.addWidget(self.grid_button);layout.addLayout(row)
        row=QHBoxLayout();self.clear_button=_ui(QPushButton, _t('清除全部取样'));self.clear_button.clicked.connect(self.clear_points)
        self.delete_button=_ui(QPushButton, _t('清除选中色块'));self.delete_button.clicked.connect(self.clear_selected)
        row.addWidget(self.clear_button);row.addWidget(self.delete_button);layout.addLayout(row)
        self.fit_button=_ui(QPushButton, _t('拟合并验证'));self.fit_button.clicked.connect(self.request_fit);layout.addWidget(self.fit_button)
        self.compare=_ui(QCheckBox, _t('预览校正后'));self.compare.toggled.connect(self.show_preview);layout.addWidget(self.compare)
        self.report=_ui(QLabel, _t('至少取样 8 个有效、不同色相的色块。请先固定片基、白平衡与曝光，避开反光、剪裁和边缘。'))
        self.report.setWordWrap(True);layout.addWidget(self.report)
        self.message=_ui(QLabel, _t('点击表中行可重新指定该色块；点击画布后自动移到下一块。'));self.message.setWordWrap(True);outer.addWidget(self.message)
        bottom=QHBoxLayout();self.export_button=_ui(QPushButton, _t('导出校正配置…'));self.export_button.clicked.connect(self.export_profile)
        self.apply_button=_ui(QPushButton, _t('应用到当前帧'));self.apply_button.clicked.connect(self.apply_result)
        self.apply_button.setProperty('role','primary')
        self.cancel_button=_ui(QPushButton, _t('关闭'));self.cancel_button.clicked.connect(self.reject)
        bottom.addWidget(self.export_button);bottom.addStretch();bottom.addWidget(self.apply_button);bottom.addWidget(self.cancel_button);outer.addLayout(bottom)
        if profile and profile.get('reference'):
            self.reference=copy.deepcopy(profile['reference']);self.name=profile.get('name',_t('自定义色卡'))
            matches=all(profile.get(k)==preview[k] for k in ('source_sha256','frame','parameter_signature'))
            self.points=copy.deepcopy(profile.get('points',[])) if matches else [None]*len(self.reference)
            if len(self.points)!=len(self.reference):self.points=[None]*len(self.reference)
            self.populate()
            if matches:self.set_result(profile)
        self.refresh_buttons();self.show_preview()

    def populate(self):
        self.table.setRowCount(len(self.reference));_ui(self.reference_label.setText, _tf('{0} · {1} 色', self.name, len(self.reference)))
        for i,patch in enumerate(self.reference):
            self.table.setItem(i,0,_ui(QTableWidgetItem, (_t('[验证] ') if patch.get('role')=='validation' else '')+patch['name']))
            self.table.setItem(i,1,_ui(QTableWidgetItem, ' / '.join(_tf('{0:g}', v) for v in patch['rgb'])))
        self.update_points();self.table.setCurrentCell(0,0)
        self.sync_role()
    def sync_role(self,*args):
        i=self.table.currentRow();self.validation_patch.blockSignals(True)
        self.validation_patch.setChecked(0<=i<len(self.reference) and self.reference[i].get('role')=='validation')
        self.validation_patch.blockSignals(False);self.validation_patch.setEnabled(not self.busy and 0<=i<len(self.reference))
    def change_role(self,checked):
        i=self.table.currentRow()
        if self.busy or not 0<=i<len(self.reference):return
        patch=self.reference[i];patch['role']='validation' if checked else 'train'
        self.table.setItem(i,0,_ui(QTableWidgetItem, (_t('[验证] ') if checked else '')+patch['name']))
        self.invalidate();_ui(self.message.setText, _t('用途已更改；训练至少 8 色，独立验证至少 3 色。验证色块只决定是否通过，不参与拟合。'))
    def import_reference(self):
        path,_=_ui(QFileDialog.getOpenFileName, self,_t('选择色卡 sRGB 参考值'),str(self.samples_path),'CSV (*.csv)')
        if path:self.load_reference(path)
    def load_reference(self,path):
        try:
            reference=calibration.read_reference(path)
            self.reference=reference;self.points=[None]*len(reference);self.name=Path(path).stem
            self.grid_mode=False;self.invalidate();self.populate()
            _ui(self.message.setText, _t('已载入参考，请点击每块中心，或两点框选网格。演示参考仅对应随附合成演示图。'))
        except Exception as error:_ui(self.message.setText, str(error))
    def invalidate(self):
        self.result=None;self.compare.setChecked(False);_ui(self.report.setText, _t('取样有变化，需要重新拟合。'))
        for i in range(self.table.rowCount()):self.table.setItem(i,3,_ui(QTableWidgetItem, '—'))
        self.refresh_buttons();self.show_preview()
    def update_points(self):
        for i,point in enumerate(self.points):
            self.table.setItem(i,2,_ui(QTableWidgetItem, _t('未取样') if point is None else _tf('{0:.3f}, {1:.3f}', point[0], point[1])))
        self.canvas.set_markers(self.points)
        self.refresh_buttons()
    def start_grid(self):
        if self.rows.value()*self.cols.value()!=len(self.reference):
            _ui(self.message.setText, _t('行数 × 列数必须等于参考色块数量。'));return
        self.grid=[];self.grid_mode=True;_ui(self.message.setText, _t('先点色卡外边界左上角，再点右下角；不包含外围留白，按从左到右、从上到下排列。'))
    def sample_at(self,x,y):
        if self.busy or not self.reference:return
        if self.grid_mode:
            self.grid.append([x,y])
            if len(self.grid)==1:_ui(self.message.setText, _t('再点击色卡外边界右下角。'));return
            (x0,y0),(x1,y1)=self.grid;self.grid_mode=False
            if x1-x0<.03 or y1-y0<.03:_ui(self.message.setText, _t('框选方向或区域无效，请重新框选。'));return
            self.points=[[x0+(c+.5)*(x1-x0)/self.cols.value(),y0+(r+.5)*(y1-y0)/self.rows.value()]
                         for r in range(self.rows.value()) for c in range(self.cols.value())]
        else:
            index=max(0,self.table.currentRow());self.points[index]=[float(x),float(y)]
            self.table.setCurrentCell(min(index+1,len(self.points)-1),0)
        self.invalidate();self.update_points();_ui(self.message.setText, _tf('已取样 {0} / {1}，可点表中行重新取样。', sum(p is not None for p in self.points), len(self.points)))
    def clear_points(self):
        self.points=[None]*len(self.reference);self.grid_mode=False;self.invalidate();self.update_points()
    def clear_selected(self):
        index=self.table.currentRow()
        if 0<=index<len(self.points):self.points[index]=None;self.invalidate();self.update_points()
    def refresh_buttons(self):
        train=sum(p is not None and r.get('role','train')=='train' for r,p in zip(self.reference,self.points))
        held=sum(p is not None and r.get('role')=='validation' for r,p in zip(self.reference,self.points))
        has_held=any(r.get('role')=='validation' for r in self.reference)
        self.fit_button.setEnabled(not self.busy and train>=8 and (not has_held or held>=3))
        allowed=bool(self.result and self.result.get('report',{}).get('can_apply'))
        self.apply_button.setEnabled(not self.busy and allowed);self.export_button.setEnabled(not self.busy and allowed)
        self.compare.setEnabled(not self.busy and self.result is not None)
        for widget in (self.import_button,self.demo_button,self.clear_button,self.delete_button,self.grid_button,self.rows,self.cols,self.table,self.canvas):widget.setEnabled(not self.busy)
        self.sync_role()
    def set_busy(self,busy):
        self.busy=busy;self.refresh_buttons()
        if busy:_ui(self.message.setText, _t('正在使用全尺寸原图取样、拟合和留一验证…'))
    def request_fit(self):
        self.set_busy(True);self.fitRequested.emit(copy.deepcopy(self.reference),copy.deepcopy(self.points),self.name)
    def set_result(self,result):
        self.result=result;self.set_busy(False);report=result['report']
        text=_tf('训练有效色块 {0} / 总共 {1}\n训练 RGB RMSE {2:.2f} → {3:.2f}；留一 {4:.2f}\n', report['valid'], report['total'], report['before_rmse'], report['after_rmse'], report['cv_rmse'])
        if 'before_de00' in report:
            text+=_tf('训练平均 ΔE00 {0:.2f} → {1:.2f}；留一 {2:.2f}\n', report['before_de00']['mean'], report['after_de00']['mean'], report['cv_de00']['mean'])
        validation=report.get('validation')
        if validation:
            text+=_tf('独立验证 {0} 色：RGB {1:.2f} → {2:.2f}\n平均 ΔE00 {3:.2f} → {4:.2f}；P95 {5:.2f} → {6:.2f}\n', validation['valid'], validation['before_rmse'], validation['after_rmse'], validation['before_de00']['mean'], validation['after_de00']['mean'], validation['before_de00']['p95'], validation['after_de00']['p95'])
        else:text+=_t('未提供独立验证色块；留一验证不等于独立实拍验证。\n')
        text+=(_t('校验通过，可预览并应用。') if report['can_apply'] else _t('禁止应用：')+'；'.join(report.get('rejection_reasons') or [_t('旧配置留一验证未通过')]))
        _ui(self.report.setText, text)
        for j,i in enumerate(report['used_indexes']):
            self.table.setItem(i,3,_ui(QTableWidgetItem, _tf('{0:.1f} → {1:.1f}', report['per_patch_before'][j], report['per_patch_after'][j])))
        if validation:
            for j,i in enumerate(validation['indexes']):
                self.table.setItem(i,3,_ui(QTableWidgetItem, _tf('{0:.1f} → {1:.1f}', validation['per_patch_before'][j], validation['per_patch_after'][j])))
        for i,fraction in enumerate(result.get('clipped_fractions',[])):
            if fraction>.1:self.table.setItem(i,3,_ui(QTableWidgetItem, _t('未取样 / 剪裁剔除')))
        self.compare.setChecked(True);self.show_preview();_ui(self.message.setText, _t('可开关预览校正后比较；应用后仍可撤销、调强度或清除。'))
    def show_preview(self):
        space=self.preview.get('working_space','srgb')
        value=calibration.apply(self.linear,self.result,working_space=space) if self.result and self.compare.isChecked() else self.linear
        if space=='prophoto':
            from working_color import display
            value=display(value,space)
        else:value=calibration.encode(value)
        pixels=(value*255+.5).astype(np.uint8)
        import colorio
        from input_color import display_working
        linear=calibration.apply(self.linear,self.result,working_space=space) if self.result and self.compare.isChecked() else self.linear
        shown=display_working(colorio.encode(linear,space),space,self.preview.get('monitor'))
        self.canvas.set_pixels(pixels,display=shown)
        self.canvas.set_markers(self.points)
    def apply_result(self):
        if self.result and self.result['report']['can_apply']:self.applied.emit(copy.deepcopy(self.result));self.accept()
    def export_profile(self):
        folder=Path(self.parent().output_edit.text()) if hasattr(self.parent(),'output_edit') else Path.home()
        path,_=_ui(QFileDialog.getSaveFileName, self,_t('保存可复用校正配置'),str(folder/'chart-calibration.json'),'JSON (*.json)')
        if path:
            try:
                import desktop_model as model
                if not path.lower().endswith('.json'):path+='.json'
                model.write_json(path,self.result);_ui(self.message.setText, _t('已保存校正配置：')+path)
            except Exception as error:_ui(self.message.setText, str(error))
    def reject(self):
        if self.busy:_ui(self.message.setText, _t('请等待当前拟合结束后关闭。'));return
        super().reject()
    def closeEvent(self,event):
        if self.busy:event.ignore()
        else:event.accept()
