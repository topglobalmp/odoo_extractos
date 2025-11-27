# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
from odoo.exceptions import ValidationError


class ExtractosTipoExtracto(models.Model):
    _name = 'extractos.tipo_extracto'
    _description = 'Tipo de Extracto Bancario'
    _order = 'name'

    name = fields.Char(string='Nombre', required=True, help='Nombre del tipo de extracto (ej: Banco Santander, BBVA, etc.)')
    formato = fields.Selection([
        ('pdf', 'PDF'),
        ('xls', 'XLS'),
        ('xlsx', 'XLSX'),
        ('csv', 'CSV'),
        ('txt', 'TXT'),
    ], string='Formato', required=True, default='xlsx', help='Formato del archivo de extracto')
    
    skiprows = fields.Integer(
        string='Filas a Saltar',
        default=0,
        help='Número de filas a saltar desde el inicio del archivo antes de leer los datos'
    )
    
    first_row_headers = fields.Boolean(
        string='Primera Fila es Cabecera',
        default=True,
        help='Si está marcado, la primera fila después de skiprows contiene los nombres de las columnas'
    )
    
    usecols = fields.Char(
        string='Columnas a Usar',
        help='Rango de columnas a usar (ej: C:M para columnas C a M). Dejar vacío para usar todas las columnas'
    )
    
    active = fields.Boolean(string='Activo', default=True)
    
    extracto_ids = fields.One2many('extractos.extracto', 'tipo_extracto_id', string='Extractos')
    cartera_ids = fields.One2many('extractos.cartera', 'tipo_extracto_id', string='Carteras')
    
    @api.constrains('usecols')
    def _check_usecols(self):
        """Valida el formato de usecols"""
        for record in self:
            if record.usecols:
                # Validar formato básico (ej: C:M, A:Z, etc.)
                import re
                pattern = r'^[A-Z]+:[A-Z]+$'
                if not re.match(pattern, record.usecols.strip().upper()):
                    raise ValidationError(_('El formato de columnas debe ser como "C:M" (letra:letra)'))

