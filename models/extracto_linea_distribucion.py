# -*- coding: utf-8 -*-

from odoo import models, fields, api, _


class ExtractosExtractoLineaDistribucion(models.Model):
    _name = 'extractos.extracto_linea_distribucion'
    _description = 'Distribución de Línea de Extracto'
    _order = 'extraordinario desc, orden asc'

    linea_id = fields.Many2one(
        'extractos.extracto_linea',
        string='Línea de Extracto',
        required=True,
        ondelete='cascade'
    )
    
    orden = fields.Integer(string='Orden', required=True, default=1)
    currency_id = fields.Many2one(related='linea_id.currency_id', string='Moneda', store=True)
    
    fecha = fields.Date(string='Fecha Cuota', required=True)
    fecha_pago = fields.Date(string='Fecha Pago', default=lambda self: fields.Date.today())
    
    importe = fields.Monetary(string='Importe', currency_field='currency_id', required=True)
    importe_pagado = fields.Monetary(
        string='Importe Pagado',
        currency_field='currency_id',
        default=0.0
    )
    
    concepto_id = fields.Many2one(
        'linx.import.pagos.distribucion.conceptos',
        string='Concepto',
        required=True
    )
    
    cuota_id = fields.Many2one('linx.cuota', string='Cuota')
    
    enabled = fields.Boolean(string='Habilitado', default=True)
    extraordinario = fields.Boolean(string='Extraordinario', default=False)
    pagado_parcial = fields.Boolean(string='Pago Parcial', default=False)
    
    def action_eliminar(self):
        """Elimina una línea de distribución y recalcula"""
        linea = self.linea_id
        self.unlink()
        if linea:
            linea.actualiza_lista_distribucion()
            return linea.open_action_distribucion()
        return True

