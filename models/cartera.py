# -*- coding: utf-8 -*-

from odoo import models, fields, api, _


class ExtractosCartera(models.Model):
    _name = 'extractos.cartera'
    _description = 'Cartera de Extractos'
    _order = 'name'

    name = fields.Char(string='Nombre', required=True, compute='_compute_name', store=True)
    prestamista_id = fields.Many2one(
        'res.partner',
        string='Prestamista',
        required=True,
        domain="[('category_id.name', '=', 'Prestamista')]",
        help='Prestamista asociado a esta cartera'
    )
    tipo_extracto_id = fields.Many2one(
        'extractos.tipo_extracto',
        string='Tipo de Extracto',
        required=True,
        help='Tipo de extracto que se utilizará para esta cartera'
    )
    
    extracto_ids = fields.One2many('extractos.extracto', 'cartera_id', string='Extractos')
    extracto_count = fields.Integer(string='Número de Extractos', compute='_compute_extracto_count')
    
    active = fields.Boolean(string='Activo', default=True)
    
    @api.depends('prestamista_id', 'tipo_extracto_id')
    def _compute_name(self):
        for record in self:
            if record.prestamista_id and record.tipo_extracto_id:
                record.name = f"{record.prestamista_id.name} - {record.tipo_extracto_id.name}"
            else:
                record.name = _('Nueva Cartera')
    
    @api.depends('extracto_ids')
    def _compute_extracto_count(self):
        for record in self:
            record.extracto_count = len(record.extracto_ids)
    
    def action_view_extractos(self):
        """Abre la vista de extractos de esta cartera"""
        self.ensure_one()
        return {
            'name': _('Extractos de %s') % self.name,
            'type': 'ir.actions.act_window',
            'res_model': 'extractos.extracto',
            'view_mode': 'tree,form',
            'domain': [('cartera_id', '=', self.id)],
            'context': {'default_cartera_id': self.id},
        }

