# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
from odoo.exceptions import UserError
import logging
import re
from dateutil.relativedelta import relativedelta

_logger = logging.getLogger(__name__)


class ExtractosExtractoLinea(models.Model):
    _name = 'extractos.extracto_linea'
    _description = 'Línea de Extracto'
    _order = 'fecha desc, id desc'

    extracto_id = fields.Many2one(
        'extractos.extracto',
        string='Extracto',
        required=True,
        ondelete='cascade'
    )
    cartera_id = fields.Many2one(
        related='extracto_id.cartera_id',
        string='Cartera',
        store=True,
        readonly=True
    )
    prestamista_id = fields.Many2one(
        related='extracto_id.prestamista_id',
        string='Prestamista',
        store=True,
        readonly=True
    )
    
    fecha = fields.Date(string='Fecha', required=True)
    importe = fields.Monetary(string='Importe', currency_field='currency_id', required=True)
    currency_id = fields.Many2one('res.currency', default=lambda self: self.env.company.currency_id)
    concepto = fields.Char(string='Concepto')
    observaciones = fields.Text(string='Observaciones')
    
    prestamo_id = fields.Many2one(
        'linx.prestamo',
        string='Préstamo',
        domain="[('prestamista_ids.partner_id', '=', prestamista_id)]",
        help='Préstamo al que se asignará este pago'
    )
    
    state = fields.Selection([
        ('pending', 'Pendiente'),
        ('discarded', 'Descartada'),
        ('processed', 'Procesada')
    ], string='Estado', default='pending', required=True)
    
    auto_asignado = fields.Boolean(
        string='Auto-Asignado',
        default=False,
        help='Indica si el préstamo fue asignado automáticamente y necesita validación'
    )
    revisado = fields.Boolean(string='Revisado', default=False)
    
    # Campos para distribución
    # fecha_contable = fields.Date(string='Fecha Contable', default=lambda self: fields.Date.today())
    # fecha_contable_alternativa = fields.Date(string='Fecha Cont. Efect.')
    fecha_calculo = fields.Date(string='Fecha Cálculo', default=lambda self: fields.Date.today())
    aplicar_penalizaciones = fields.Boolean(string='Aplicar Penalizaciones', default=True)
    aplicar_moras = fields.Boolean(string='Aplicar Moras', default=True)
    
    distribucion_ids = fields.One2many(
        'extractos.extracto_linea_distribucion',
        'linea_id',
        string='Distribución'
    )
    importe_distribuido = fields.Monetary(
        string='Importe Distribuido',
        currency_field='currency_id',
        compute='_compute_importe_distribuido'
    )
    pago_parcial = fields.Boolean(string='Pago Parcial', default=False)
    
    pago_id = fields.Many2one('linx.pago', string='Pago Creado', readonly=True)
    
    # Campos temporales para extraordinarios
    importe_extraordinario = fields.Monetary(string='Importe Extraordinario', currency_field='currency_id')
    concepto_extraordinario = fields.Char(string='Concepto Extraordinario')
    
    @api.depends('distribucion_ids', 'distribucion_ids.importe_pagado')
    def _compute_importe_distribuido(self):
        for record in self:
            record.importe_distribuido = sum(record.distribucion_ids.mapped('importe_pagado'))
    
    def actualiza_lista_distribucion_wrapper(self):
        """Wrapper para actualizar lista de distribución desde la vista"""
        self.actualiza_lista_distribucion()
        return self.open_action_distribucion()
    
    def action_marcar_revisado(self):
        """Marca/desmarca como revisado"""
        self.ensure_one()
        self.write({'revisado': not self.revisado})
    
    def action_add_extraordinario(self):
        """Añade un extraordinario a la distribución"""
        self.ensure_one()
        if not self.importe_extraordinario or not self.concepto_extraordinario:
            raise UserError(_('Debe indicar importe y concepto para el extraordinario.'))
        
        # Buscar o crear concepto
        concepto = self.env['linx.import.pagos.distribucion.conceptos'].search([
            ('name', '=', self.concepto_extraordinario)
        ], limit=1)
        if not concepto:
            concepto = self.env['linx.import.pagos.distribucion.conceptos'].create({
                'name': self.concepto_extraordinario
            })
        
        # Obtener siguiente orden
        max_orden = max(self.distribucion_ids.mapped('orden')) if self.distribucion_ids else 0
        
        self.env['extractos.extracto_linea_distribucion'].create({
            'linea_id': self.id,
            'orden': max_orden + 1,
            'fecha': self.fecha,
            'importe': self.importe_extraordinario,
            'concepto_id': concepto.id,
            'extraordinario': True,
            'enabled': True
        })
        
        # Limpiar campos temporales
        self.write({
            'importe_extraordinario': 0,
            'concepto_extraordinario': ''
        })
        
        # Redistribuir
        self.distribuye()
    
    def open_action_distribucion(self):
        """Abre la vista de distribución"""
        return {
            'name': _('Distribución del pago'),
            'type': 'ir.actions.act_window',
            'res_model': 'extractos.extracto_linea',
            'view_mode': 'form',
            'res_id': self.id,
            'target': 'new',
            'context': {'dialog_size': 'large'},
            'views': [(self.env.ref('extractos.view_extracto_linea_distribucion_form').id, 'form')],
        }
    
    def auto_asignar_prestamo(self):
        """Intenta asignar automáticamente un préstamo a esta línea"""
        self.ensure_one()
        if self.prestamo_id or not self.observaciones:
            return
        
        prestamista_id = self.prestamista_id.id if self.prestamista_id else None
        if not prestamista_id:
            return
        
        # 1. Buscar por concepto/observaciones en pagos previos de la misma cartera
        pagos_previos = self.env['extractos.extracto_linea'].search([
            ('cartera_id', '=', self.cartera_id.id),
            ('prestamo_id', '!=', False),
            ('observaciones', 'ilike', self.observaciones),
            ('importe', '=', self.importe),
            ('id', '!=', self.id)
        ], order='id desc', limit=1)
        
        if pagos_previos:
            self.prestamo_id = pagos_previos.prestamo_id
            self.auto_asignado = True
            self.actualiza_lista_distribucion()
            return
        
        # 2. Buscar número de préstamo en observaciones (ej: HIS 12345)
        match = re.search(r'HIS\s*(\d+)', self.observaciones or '', re.IGNORECASE)
        if match:
            op_number = match.group(1)
            prestamo = self.env['linx.prestamo'].search([
                ('name', 'ilike', f'HIS {op_number}'),
                ('prestamista_id', '=', prestamista_id)
            ], limit=1)
            if prestamo:
                self.prestamo_id = prestamo
                self.auto_asignado = True
                self.actualiza_lista_distribucion()
                return
        
        # 3. Buscar por DNI/NIF en observaciones
        match_dni = re.search(r'(\d{8}[A-Z]?)', self.observaciones or '')
        if match_dni:
            dni = match_dni.group(1)
            # Buscar partner
            partner = self.env['res.partner'].search([('vat', 'ilike', dni)], limit=1)
            if partner:
                # Buscar préstamo activo del partner con este prestamista
                prestamo_partner = self.env['linx.prestamo_partner'].search([
                    ('partner_id', '=', partner.id),
                    ('prestamo_id.prestamista_id', '=', prestamista_id),
                    ('prestamo_id.state', 'in', ['formalized', 'confirmed'])
                ], limit=1)
                if prestamo_partner:
                    self.prestamo_id = prestamo_partner.prestamo_id
                    self.auto_asignado = True
                    self.actualiza_lista_distribucion()
                    return
        
        # 4. Buscar por nombre en observaciones
        # Extraer posibles nombres (palabras con mayúsculas)
        palabras = re.findall(r'\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+\b', self.observaciones or '')
        for palabra in palabras[:3]:  # Limitar a 3 palabras para no hacer demasiadas búsquedas
            if len(palabra) > 3:
                partner = self.env['res.partner'].search([
                    ('name', 'ilike', palabra),
                    ('category_id.name', '=', 'Cliente')
                ], limit=5)
                for p in partner:
                    prestamo_partner = self.env['linx.prestamo_partner'].search([
                        ('partner_id', '=', p.id),
                        ('prestamo_id.prestamista_id', '=', prestamista_id),
                        ('prestamo_id.state', 'in', ['formalized', 'confirmed'])
                    ], limit=1)
                    if prestamo_partner:
                        self.prestamo_id = prestamo_partner.prestamo_id
                        self.auto_asignado = True
                        self.actualiza_lista_distribucion()
                        return
    
    def actualiza_lista_distribucion(self):
        """Actualiza la lista de distribución del pago (similar a ActualizaListaDistribucion de linx)"""
        if "NewId" in str(self.id):
            return
        
        if not self.prestamo_id:
            return
        if not self.fecha:
            return
        
        self.write({'fecha_calculo': self.fecha})
        _logger.debug('ActualizaListaDistribucion %s' % self.prestamo_id.name)
        
        items = []
        limit = 25
        count = 0
        
        cuotas_no_pagadas = self.prestamo_id.cuota_ids.filtered(
            lambda x: x.realmente_pagada == False
        ).sorted(key=lambda x: x.numero)
        
        for cuota in cuotas_no_pagadas:
            # Ajustar centimo de la cuota si es necesario
            if cuota.importe != cuota.capital + cuota.interes:
                diff = cuota.importe - (cuota.capital + cuota.interes)
                if diff > 0 and diff < 0.02:
                    cuota.interes += diff
                elif diff < 0 and diff > -0.02:
                    cuota.interes -= diff
            
            # Penalización
            penalizacion_a_fecha = cuota.penalizacion_a_fecha(self.fecha_calculo)
            penalizacion_pendiente = penalizacion_a_fecha - cuota.penalizacion_pagada
            if penalizacion_pendiente >= 0.02:
                items.append({
                    'importe': penalizacion_pendiente,
                    'fecha': cuota.fecha,
                    'concepto': 'Penalización',
                    'cuota': cuota.id
                })
            
            # Mora
            mora_pendiente = cuota.get_mora_a_fecha(self.fecha_calculo) - cuota.mora_pagada
            if mora_pendiente >= 0.02:
                items.append({
                    'importe': mora_pendiente,
                    'fecha': cuota.fecha,
                    'concepto': 'Mora',
                    'cuota': cuota.id
                })
            
            # Interés
            interes_pendiente = cuota.interes - cuota.interes_pagado
            if interes_pendiente >= 0.02:
                items.append({
                    'importe': interes_pendiente,
                    'fecha': cuota.fecha,
                    'concepto': 'Interés',
                    'cuota': cuota.id
                })
            
            # Capital
            capital_pendiente = cuota.capital - cuota.capital_pagado
            if capital_pendiente >= 0.02:
                items.append({
                    'importe': capital_pendiente,
                    'fecha': cuota.fecha,
                    'concepto': 'Capital',
                    'cuota': cuota.id
                })
            
            count += 1
            if count >= limit:
                break
        
        # Obtener distribución actual
        cache = {}
        index = 0
        current_dist = self.distribucion_ids.sorted(
            key=lambda x: (-int(x.extraordinario), x.orden)
        )
        
        # Procesar extraordinarios primero
        extraordinarias = self.distribucion_ids.filtered(lambda x: x.extraordinario == True)
        for item in extraordinarias:
            concepto_name = item.concepto_id.name if item.concepto_id else ''
            if concepto_name in cache:
                concepto = cache[concepto_name]
            else:
                concepto = self.env['linx.import.pagos.distribucion.conceptos'].search([
                    ('name', '=', concepto_name)
                ], limit=1)
                if not concepto:
                    concepto = self.env['linx.import.pagos.distribucion.conceptos'].create({
                        'name': concepto_name
                    })
                cache[concepto_name] = concepto
            
            if current_dist and index < len(current_dist):
                current_dist[index].write({
                    'orden': index + 1,
                    'fecha': item.fecha,
                    'importe': item.importe,
                    'extraordinario': True,
                    'concepto_id': concepto.id,
                    'enabled': True
                })
            else:
                self.env['extractos.extracto_linea_distribucion'].create({
                    'orden': index + 1,
                    'fecha': item.fecha,
                    'linea_id': self.id,
                    'importe': item.importe,
                    'extraordinario': True,
                    'concepto_id': concepto.id,
                    'enabled': True
                })
            index += 1
        
        # Procesar items normales
        for item in items:
            concepto_name = item['concepto']
            if concepto_name in cache:
                concepto = cache[concepto_name]
            else:
                concepto = self.env['linx.import.pagos.distribucion.conceptos'].search([
                    ('name', '=', concepto_name)
                ], limit=1)
                if not concepto:
                    concepto = self.env['linx.import.pagos.distribucion.conceptos'].create({
                        'name': concepto_name
                    })
                cache[concepto_name] = concepto
            
            if current_dist and index < len(current_dist):
                current_dist[index].write({
                    'orden': index + 1,
                    'fecha': item['fecha'],
                    'importe': item['importe'],
                    'concepto_id': concepto.id,
                    'cuota_id': item['cuota'],
                    'enabled': True,
                    'extraordinario': False
                })
            else:
                self.env['extractos.extracto_linea_distribucion'].create({
                    'orden': index + 1,
                    'fecha': item['fecha'],
                    'linea_id': self.id,
                    'importe': item['importe'],
                    'concepto_id': concepto.id,
                    'cuota_id': item['cuota'],
                    'enabled': True,
                    'extraordinario': False
                })
            index += 1
        
        # Distribuir el importe
        self.distribuye()
    
    def distribuye(self):
        """Distribuye el importe entre las líneas de distribución"""
        _logger.debug('Distribuyendo %s' % self.prestamo_id.name if self.prestamo_id else 'Sin préstamo')
        self.ensure_one()
        if not self.distribucion_ids:
            return
        
        importe = self.importe
        importe_distribuido = 0
        
        lista = self.distribucion_ids.sorted(
            key=lambda x: (-int(x.extraordinario), x.orden)
        )
        
        for item in lista:
            if importe < 0.01:
                item.write({'importe_pagado': 0, 'pagado_parcial': False})
                continue
            if item.enabled == False:
                item.write({'importe_pagado': 0, 'pagado_parcial': False})
                continue
            if self.aplicar_moras == False and item.concepto_id.name == 'Mora':
                item.write({'importe_pagado': 0, 'pagado_parcial': False})
                continue
            if self.aplicar_penalizaciones == False and item.concepto_id.name == 'Penalización':
                item.write({'importe_pagado': 0, 'pagado_parcial': False})
                continue
            
            diff = round(item.importe, 2) - round(importe, 2)
            if diff > 0.01:
                # Pago parcial
                item.write({'importe_pagado': importe, 'pagado_parcial': True})
                importe_distribuido += importe
                importe = 0
                self.write({'pago_parcial': True})
            elif diff < 0.01:
                # Pago completo y sobra
                item.write({'importe_pagado': item.importe, 'pagado_parcial': False})
                importe_distribuido += item.importe
                self.write({'pago_parcial': False})
                importe -= item.importe
            else:
                # Pago completo exacto
                item.write({'importe_pagado': item.importe, 'pagado_parcial': False})
                importe_distribuido += item.importe
                self.write({'pago_parcial': False})
                importe = 0
        
        self.write({'importe_distribuido': importe_distribuido})
        self.revisado = not self.pago_parcial
    
    def action_descartar(self):
        """Descarta esta línea"""
        self.ensure_one()
        self.write({'state': 'discarded'})
    
    def action_restaurar(self):
        """Restaura una línea descartada"""
        self.ensure_one()
        self.write({'state': 'pending'})
    
    def action_procesar(self):
        """Procesa la línea creando el pago en linx"""
        self.ensure_one()
        if not self.prestamo_id:
            raise UserError(_('Debe asignar un préstamo antes de procesar.'))
        if self.state == 'processed':
            raise UserError(_('Esta línea ya ha sido procesada.'))
        
        # Crear linx.pago
        pago_vals = {
            'prestamo_id': self.prestamo_id.id,
            'fecha': self.fecha_calculo or self.fecha,
            'importe': self.importe,
            'comentarios': self.observaciones or self.concepto or '',
            'currency_id': self.currency_id.id,
        }
        pago = self.env['linx.pago'].create(pago_vals)
        self.pago_id = pago
        
        # Crear linx.distribucion_pago
        for dist in self.distribucion_ids.filtered(lambda d: d.importe_pagado > 0):
            tipo = 'otros'
            concepto_name = dist.concepto_id.name if dist.concepto_id else ''
            if concepto_name == 'Capital':
                tipo = 'capital'
            elif concepto_name == 'Interés':
                tipo = 'interes'
            elif concepto_name == 'Mora':
                tipo = 'mora'
            elif concepto_name == 'Penalización':
                tipo = 'penalizacion'
            
            self.env['linx.distribucion_pago'].create({
                'pago_id': pago.id,
                'prestamo_id': self.prestamo_id.id,
                'cuota_id': dist.cuota_id.id if dist.cuota_id else False,
                'importe': dist.importe_pagado,
                'tipo': tipo,
                'concepto_id': dist.concepto_id.id if dist.concepto_id else False,
                'fecha': self.fecha_calculo or self.fecha,
                'fecha_cuota': dist.cuota_id.fecha if dist.cuota_id else False,
            })
        
        self.write({'state': 'processed'})
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Línea procesada'),
                'message': _('Se ha creado el pago %s para el préstamo %s') % (pago.name, self.prestamo_id.name),
                'type': 'success',
                'sticky': False,
            }
        }
    
    @api.onchange('prestamo_id')
    def _onchange_prestamo_id(self):
        """Cuando se asigna un préstamo, actualizar distribución"""
        if self.prestamo_id:
            self.actualiza_lista_distribucion()
    
    @api.onchange('fecha_calculo')
    def _onchange_fecha_calculo(self):
        """Cuando cambia la fecha de cálculo, actualizar distribución"""
        if self.prestamo_id:
            self.actualiza_lista_distribucion()
    
    @api.onchange('distribucion_ids')
    def _onchange_distribucion_ids(self):
        """Cuando cambia la distribución, redistribuir"""
        if self.distribucion_ids:
            self.distribuye()

