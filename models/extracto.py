# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
import base64
import io
import logging
import json
import re
import pandas as pd
from datetime import datetime

_logger = logging.getLogger(__name__)


class ExtractosExtracto(models.Model):
    _name = 'extractos.extracto'
    _description = 'Extracto Bancario'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'fecha desc'

    name = fields.Char(string='Referencia', required=True, default=lambda self: _('Nuevo extracto'))
    fecha = fields.Date(string='Fecha', required=True, default=fields.Date.today, tracking=True)
    cartera_id = fields.Many2one(
        'extractos.cartera',
        string='Cartera',
        required=True,
        tracking=True,
        help='Cartera a la que pertenece este extracto'
    )
    tipo_extracto_id = fields.Many2one(
        related='cartera_id.tipo_extracto_id',
        string='Tipo de Extracto',
        store=True,
        readonly=True
    )
    prestamista_id = fields.Many2one(
        related='cartera_id.prestamista_id',
        string='Prestamista',
        store=True,
        readonly=True
    )
    
    file = fields.Binary(string='Archivo', required=True, attachment=True)
    file_name = fields.Char(string='Nombre del Archivo')
    
    # Líneas del extracto
    linea_ids = fields.One2many('extractos.extracto_linea', 'extracto_id', string='Líneas')
    
    # Campos Many2many computed para filtrar por estado (workaround para el bug de domain en One2many)
    lineas_pendientes = fields.Many2many(
        'extractos.extracto_linea',
        'extracto_linea_pendiente_rel',
        'extracto_id',
        'linea_id',
        string='Líneas Pendientes',
        compute='_compute_lineas_by_state',
        store=False
    )
    lineas_descartadas = fields.Many2many(
        'extractos.extracto_linea',
        'extracto_linea_descartada_rel',
        'extracto_id',
        'linea_id',
        string='Líneas Descartadas',
        compute='_compute_lineas_by_state',
        store=False
    )
    lineas_procesadas = fields.Many2many(
        'extractos.extracto_linea',
        'extracto_linea_procesada_rel',
        'extracto_id',
        'linea_id',
        string='Líneas Procesadas',
        compute='_compute_lineas_by_state',
        store=False
    )
    
    count_lineas_pendientes = fields.Integer(
        string='Líneas Pendientes',
        compute='_compute_lineas_count',
        store=True
    )
    count_lineas_descartadas = fields.Integer(
        string='Líneas Descartadas',
        compute='_compute_lineas_count',
        store=True
    )
    count_lineas_procesadas = fields.Integer(
        string='Líneas Procesadas',
        compute='_compute_lineas_count',
        store=True
    )
    
    tiene_lineas_pendientes_sin_prestamo = fields.Boolean(
        string='Tiene Líneas Pendientes Sin Préstamo',
        compute='_compute_tiene_lineas_pendientes_sin_prestamo',
        help='Indica si hay líneas pendientes sin préstamo asignado para usar IA'
    )
    
    state = fields.Selection([
        ('draft', 'Borrador'),
        ('imported', 'Importado'),
        ('processed', 'Procesado')
    ], string='Estado', default='draft', tracking=True)
    
    @api.depends('linea_ids', 'linea_ids.state')
    def _compute_lineas_by_state(self):
        """Calcula las líneas filtradas por estado usando Many2many computed"""
        for record in self:
            record.lineas_pendientes = record.linea_ids.filtered(lambda l: l.state == 'pending')
            record.lineas_descartadas = record.linea_ids.filtered(lambda l: l.state == 'discarded')
            record.lineas_procesadas = record.linea_ids.filtered(lambda l: l.state == 'processed')
    
    @api.depends('linea_ids', 'linea_ids.state', 'linea_ids.prestamo_id')
    def _compute_lineas_count(self):
        for record in self:
            record.count_lineas_pendientes = len(record.linea_ids.filtered(lambda l: l.state == 'pending'))
            record.count_lineas_descartadas = len(record.linea_ids.filtered(lambda l: l.state == 'discarded'))
            record.count_lineas_procesadas = len(record.linea_ids.filtered(lambda l: l.state == 'processed'))
    
    @api.depends('linea_ids', 'linea_ids.state', 'linea_ids.prestamo_id')
    def _compute_tiene_lineas_pendientes_sin_prestamo(self):
        for record in self:
            record.tiene_lineas_pendientes_sin_prestamo = bool(
                record.linea_ids.filtered(lambda l: l.state == 'pending' and not l.prestamo_id)
            )
    
    def _fix_xlsx_empty_styles(self, file_data):
        """Arregla estilos vacíos en archivos xlsx"""
        try:
            import zipfile
            zin = zipfile.ZipFile(io.BytesIO(file_data), "r")
            zout_buffer = io.BytesIO()
            zout = zipfile.ZipFile(zout_buffer, "w", zipfile.ZIP_DEFLATED)
            
            for item in zin.infolist():
                buffer = zin.read(item.filename)
                if item.filename == "xl/styles.xml":
                    buffer = buffer.decode("utf-8").replace("<fill/>", "").encode("utf-8")
                zout.writestr(item, buffer)
            zout.close()
            zin.close()
            return zout_buffer.getvalue()
        except Exception:
            return file_data
    
    def _parse_usecols(self, usecols_str):
        """Convierte 'C:M' a formato pandas usecols"""
        if not usecols_str:
            return None
        try:
            # Convertir 'C:M' a ['C', 'D', ..., 'M']
            start, end = usecols_str.strip().upper().split(':')
            start_col = ord(start) - ord('A')
            end_col = ord(end) - ord('A')
            return list(range(start_col, end_col + 1))
        except Exception:
            return None
    
    def action_importar(self):
        """Importa el archivo según la configuración del tipo de extracto"""
        self.ensure_one()
        if not self.file:
            raise UserError(_('No se ha seleccionado ningún archivo.'))
        if not self.cartera_id or not self.cartera_id.tipo_extracto_id:
            raise UserError(_('Debe seleccionar una cartera con tipo de extracto configurado.'))
        
        tipo_extracto = self.cartera_id.tipo_extracto_id
        
        # Leer archivo
        data = base64.b64decode(self.file)
        
        # Limpiar xlsx si es necesario
        if tipo_extracto.formato in ['xlsx']:
            data = self._fix_xlsx_empty_styles(data)
        
        try:
            # Leer según formato
            if tipo_extracto.formato in ['xls']:
                engine = 'xlrd'
                import xlrd
                book = xlrd.open_workbook(file_contents=data)
                _logger.info(f"The number of worksheets is {book.nsheets}")
                # workbook = book.sheet_by_index(0)
                sheet = book.sheet_by_index(0)
                
                start_lines = tipo_extracto.skiprows
                data_list = []
                if tipo_extracto.first_row_headers:
                    l_headers = start_lines
                    start_lines = start_lines + 1
                else:
                    l_headers = tipo_extracto.skiprows
                    start_lines = tipo_extracto.skiprows + 1
                
                headers = sheet.row_values(l_headers) # Fila 16 (índice 15) son los headers
                _logger.info(headers)
                
                for row_idx in range(start_lines, sheet.nrows):
                    row_values = sheet.row_values(row_idx)
                    row_dict = {}
                    for col_idx, header in enumerate(headers):
                        if col_idx < len(row_values):
                            row_dict[header] = row_values[col_idx]
                    data_list.append(row_dict)
                df = pd.DataFrame(data_list)
            elif tipo_extracto.formato in ['xlsx']:
                engine = 'openpyxl' if tipo_extracto.formato == 'xlsx' else 'xlrd'
                usecols = self._parse_usecols(tipo_extracto.usecols)
                
                read_params = {
                    'io': io.BytesIO(data),
                    'engine': engine,
                    'skiprows': tipo_extracto.skiprows,
                    'keep_default_na': False,
                }
                
                if tipo_extracto.first_row_headers:
                    read_params['header'] = 0
                else:
                    read_params['header'] = None
                
                if usecols:
                    read_params['usecols'] = usecols
                
                df = pd.read_excel(**read_params)
                
            elif tipo_extracto.formato == 'csv':
                read_params = {
                    'filepath_or_buffer': io.BytesIO(data),
                    'skiprows': tipo_extracto.skiprows,
                    'keep_default_na': False,
                }
                if tipo_extracto.first_row_headers:
                    read_params['header'] = 0
                else:
                    read_params['header'] = None
                
                df = pd.read_csv(**read_params)
                
            elif tipo_extracto.formato == 'txt':
                # Leer como CSV con delimitador tab
                read_params = {
                    'filepath_or_buffer': io.BytesIO(data),
                    'skiprows': tipo_extracto.skiprows,
                    'sep': '\t',
                    'keep_default_na': False,
                }
                if tipo_extracto.first_row_headers:
                    read_params['header'] = 0
                else:
                    read_params['header'] = None
                
                df = pd.read_csv(**read_params)
            else:
                raise UserError(_('Formato %s no soportado aún.') % tipo_extracto.formato)
            
            # Preparar datos con información de columnas
            tipo_extracto = self.tipo_extracto_id
            _data = []
            
            # Si hay headers, obtener nombres de columnas; si no, usar índices
            if tipo_extracto.first_row_headers:
                columnas = list(df.columns)
            else:
                columnas = [str(i) for i in range(len(df.columns))]
            
            # Convertir cada fila a dict con información de columnas
            for idx, row in df.iterrows():
                item = {}
                for i, col_name in enumerate(columnas):
                    item[col_name] = row.iloc[i]
                    # También añadir por índice numérico para facilitar búsqueda
                    item[str(i)] = row.iloc[i]
                _data.append(item)
            
            _logger.info('Importando %s líneas del extracto' % len(_data))
            
            # Obtener líneas existentes de esta cartera para evitar duplicados
            existing_lines = self.env['extractos.extracto_linea'].search([
                ('extracto_id.cartera_id', '=', self.cartera_id.id),
                ('extracto_id', '!=', self.id)
            ])
            
            # Crear líneas
            nuevas_lineas = []
            tipo_extracto = self.tipo_extracto_id
            for item in _data:
                # Intentar extraer campos usando las columnas configuradas
                importe_raw = self._extract_importe(item, tipo_extracto)
                fecha = self._extract_fecha(item, tipo_extracto)
                concepto = self._extract_concepto(item, tipo_extracto)
                observaciones = self._extract_observaciones(item, tipo_extracto)
                
                # Si no se pudo extraer el importe, descartar automáticamente
                if importe_raw is None:
                    state = 'discarded'
                    importe_final = 0.0
                # Si el importe es cero o negativo, descartar automáticamente (solo nos interesan ingresos positivos)
                elif importe_raw <= 0:
                    state = 'discarded'
                    importe_final = abs(importe_raw) if importe_raw < 0 else 0.0
                else:
                    # Importe positivo: verificar si ya existe esta línea en otro extracto de la misma cartera
                    if self._existe_linea_duplicada(existing_lines, concepto, observaciones, fecha, importe_raw):
                        continue  # Saltar línea duplicada (no crear)
                    state = 'pending'
                    importe_final = importe_raw
                
                nuevas_lineas.append({
                    'extracto_id': self.id,
                    'fecha': fecha,
                    'importe': importe_final,
                    'concepto': concepto,
                    'observaciones': observaciones,
                    'state': state,
                })
            
            # Crear líneas
            if nuevas_lineas:
                self.env['extractos.extracto_linea'].create(nuevas_lineas)
                _logger.info('Creadas %s líneas nuevas' % len(nuevas_lineas))
            
            # Auto-asignar préstamos a líneas pendientes
            count_lineas_pendientes = self.linea_ids.filtered(lambda l: l.state == 'pending')
            for linea in count_lineas_pendientes:
                linea.auto_asignar_prestamo()
            
            self.state = 'imported'
            return True
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Importación completada'),
                    'message': _('Se importaron %s líneas. %s pendientes, %s descartadas.') % (
                        len(nuevas_lineas),
                        len(self.linea_ids.filtered(lambda l: l.state == 'pending')),
                        len(self.linea_ids.filtered(lambda l: l.state == 'discarded'))
                    ),
                    'type': 'success',
                    'sticky': False,
                }
            }
            
        except Exception as e:
            _logger.error("Error al importar extracto: %s", str(e), exc_info=True)
            raise UserError(_('Error al importar el archivo: %s') % str(e))
    
    def _columna_a_indice(self, columna_letra):
        """Convierte una letra de columna (A, B, C, etc.) a índice numérico (0, 1, 2, etc.)"""
        if not columna_letra:
            return None
        columna_letra = columna_letra.strip().upper()
        indice = 0
        for char in columna_letra:
            indice = indice * 26 + (ord(char) - ord('A') + 1)
        return indice - 1
    
    def _obtener_valor_por_columna(self, item, columna_letra, fallback_keys=None):
        """Obtiene el valor del item usando la columna configurada o busca por nombre de columna"""
        # Si hay columna configurada, usarla
        if columna_letra:
            indice = self._columna_a_indice(columna_letra)
            if indice is not None and isinstance(item, dict):
                # Ahora cada item tiene tanto el índice numérico como el nombre de columna (si hay headers)
                # Primero intentar con el índice numérico (siempre disponible)
                if str(indice) in item:
                    valor = item[str(indice)]
                    if valor is not None and (not isinstance(valor, str) or valor.strip()):
                        return valor
                
                # Si hay headers, también buscar por nombre de columna
                # Las claves que no son numéricas son nombres de columnas
                keys = [k for k in item.keys() if not (isinstance(k, str) and k.isdigit())]
                if indice < len(keys):
                    valor = item[keys[indice]]
                    if valor is not None and (not isinstance(valor, str) or valor.strip()):
                        return valor
        
        # Fallback: buscar por nombres comunes si se proporcionan
        if fallback_keys:
            for key in fallback_keys:
                if key in item and item[key] is not None:
                    valor = item[key]
                    if not isinstance(valor, str) or valor.strip():
                        return valor
        
        return None
    
    def _extract_importe(self, item, tipo_extracto=None):
        """Extrae el importe del item usando la columna configurada"""
        if not tipo_extracto:
            tipo_extracto = self.tipo_extracto_id
        
        valor = self._obtener_valor_por_columna(
            item, 
            tipo_extracto.columna_importe if tipo_extracto else None,
            fallback_keys=['IMPORTE', 'Importe', 'importe', 'IMPORT', 'Import', 'amount', 'Amount', 'AMOUNT']
        )
        
        if valor is not None:
            try:
                # Limpiar el valor si es string (quitar símbolos de moneda, espacios, etc.)
                if isinstance(valor, str):
                    # Preservar el signo negativo si existe
                    valor_limpio = valor.replace('€', '').replace('$', '').replace(' ', '').strip()
                    # Reemplazar coma por punto para decimales, pero preservar el signo
                    if ',' in valor_limpio and '.' not in valor_limpio:
                        valor_limpio = valor_limpio.replace(',', '.')
                    val = float(valor_limpio)
                    return val
                else:
                    val = float(valor)
                    return val
            except (ValueError, TypeError):
                pass
        return None  # Devolver None en lugar de 0.0 para distinguir entre "no encontrado" y "cero"
    
    def _extract_fecha(self, item, tipo_extracto=None):
        """Extrae la fecha del item usando la columna configurada"""
        if not tipo_extracto:
            tipo_extracto = self.tipo_extracto_id
        
        valor = self._obtener_valor_por_columna(
            item,
            tipo_extracto.columna_fecha if tipo_extracto else None,
            fallback_keys=['F. CONTABLE', 'FECHA', 'Fecha', 'fecha', 'FECHA CONTABLE', 'date', 'Date', 'DATE']
        )
        
        if valor is not None:
            try:
                # Si es un número (timestamp Excel), convertir
                if isinstance(valor, (int, float)):
                    return datetime.fromtimestamp((valor - 25569) * 86400).date()
                # Si es string, intentar parsear
                if isinstance(valor, str):
                    for fmt in ['%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y', '%Y/%m/%d', '%d/%m/%y']:
                        try:
                            return datetime.strptime(valor.strip(), fmt).date()
                        except ValueError:
                            continue
            except Exception:
                pass
        return fields.Date.today()
    
    def _extract_concepto(self, item, tipo_extracto=None):
        """Extrae el concepto del item usando la columna configurada"""
        if not tipo_extracto:
            tipo_extracto = self.tipo_extracto_id
        
        valor = self._obtener_valor_por_columna(
            item,
            tipo_extracto.columna_concepto if tipo_extracto else None,
            fallback_keys=['CONCEPTO', 'Concepto', 'concepto', 'CONCEPT', 'Concept']
        )
        
        if valor is not None:
            return str(valor)
        return ''
    
    def _extract_ordenante(self, item, tipo_extracto=None):
        """Extrae el ordenante/interviniente del item usando la columna configurada"""
        if not tipo_extracto:
            tipo_extracto = self.tipo_extracto_id
        
        valor = self._obtener_valor_por_columna(
            item,
            tipo_extracto.columna_ordenante if tipo_extracto else None,
            fallback_keys=['ORDENANTE', 'Ordenante', 'ordenante', 'INTERVINIENTE', 'Interviniente', 'interviniente']
        )
        
        if valor is not None:
            return str(valor)
        return ''
    
    def _extract_observaciones(self, item, tipo_extracto=None):
        """Extrae las observaciones del item"""
        # Las observaciones pueden venir de varias fuentes o combinarse con ordenante
        ordenante = self._extract_ordenante(item, tipo_extracto) if tipo_extracto else None
        
        valor = self._obtener_valor_por_columna(
            item,
            None,  # No hay columna específica para observaciones
            fallback_keys=['OBSERVACIONES', 'Observaciones', 'observaciones', 'OBS', 'Obs', 'DESCRIPCION', 'Descripcion', 'DETALLE', 'Detalle']
        )
        
        observaciones = str(valor) if valor is not None else ''
        
        # Si hay ordenante, añadirlo a las observaciones
        if ordenante:
            if observaciones:
                observaciones = f"{observaciones} | Ordenante: {ordenante}"
            else:
                observaciones = f"Ordenante: {ordenante}"
        
        return observaciones
    
    def _existe_linea_duplicada(self, existing_lines, concepto, observaciones, fecha, importe):
        """Verifica si ya existe una línea similar en otro extracto de la misma cartera"""
        for existing in existing_lines:
            if (existing.concepto == concepto and 
                existing.observaciones == observaciones and
                existing.fecha == fecha and
                abs(existing.importe - importe) < 0.01):
                return True
        return False
    
    def action_usar_inteligencia_artificial(self):
        """Usa IA para asociar conceptos con operaciones basándose en nombres de intervinientes"""
        self.ensure_one()
        
        _logger.info("tiene_lineas_pendientes_sin_prestamo: %s", self.tiene_lineas_pendientes_sin_prestamo)
        # Verificar que hay líneas pendientes sin préstamo asignado
        # Usar linea_ids directamente para filtrar correctamente
        todas_las_lineas = self.linea_ids
        lineas_pendientes = todas_las_lineas.filtered(lambda l: l.state == 'pending')
        count_lineas_pendientes = lineas_pendientes.filtered(lambda l: not l.prestamo_id)
        _logger.info("Total líneas: %s, Pendientes: %s, Pendientes sin préstamo: %s", 
                    len(todas_las_lineas), len(lineas_pendientes), len(count_lineas_pendientes))
        _logger.info("Líneas pendientes sin préstamo (IDs): %s", count_lineas_pendientes.mapped('id'))
        if not count_lineas_pendientes:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Sin líneas pendientes'),
                    'message': _('No hay líneas pendientes sin préstamo asignado para procesar con IA.'),
                    'type': 'warning',
                    'sticky': False,
                }
            }
        
        # Verificar que hay prestamista
        if not self.prestamista_id:
            raise UserError(_('Debe tener un prestamista asignado para usar IA.'))
        
        # Obtener todas las operaciones del prestamista con sus intervinientes
        prestamos = self.env['linx.prestamo'].search([
            ('prestamista_ids.partner_id', '=', self.prestamista_id.id),
            ('state', 'in', ['formalized', 'confirmed', 'draft'])
        ])
        
        if not prestamos:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Sin operaciones'),
                    'message': _('No hay operaciones formalizadas para el prestamista %s.') % self.prestamista_id.name,
                    'type': 'warning',
                    'sticky': False,
                }
            }
        
        # Preparar datos de operaciones
        operaciones_data = []
        for prestamo in prestamos:
            intervinientes = []
            for interviniente in prestamo.interviniente_ids:
                intervinientes.append({
                    'nombre': interviniente.partner_id.name or '',
                    'nif': interviniente.partner_id.vat or '',
                })
            
            operaciones_data.append({
                'id': prestamo.id,
                'nombre': prestamo.name or '',
                'intervinientes': intervinientes
            })
        
        # Preparar datos de conceptos (líneas pendientes)
        conceptos_data = []
        for linea in count_lineas_pendientes:
            conceptos_data.append({
                'id': linea.id,
                'concepto': linea.concepto or '',
                'observaciones': linea.observaciones or '',
                'importe': linea.importe,
                'fecha': str(linea.fecha) if linea.fecha else '',
            })
        
        # Construir prompt para ChatGPT
        system_prompt = """Eres un asistente experto en asociar pagos bancarios con préstamos hipotecarios.
        Tu tarea es analizar conceptos de pagos y asociarlos con operaciones basándote en los nombres de los intervinientes.
        Los nombres pueden estar escritos de forma diferente (con o sin acentos, mayúsculas/minúsculas, abreviaciones, etc.).
        Debes devolver SOLO un JSON válido con la siguiente estructura:
        {
        "asociaciones": [
            {"concepto_id": <id_del_concepto>, "operacion_id": <id_de_la_operacion>},
            ...
        ]
        }
        Si no puedes asociar un concepto con certeza, no lo incluyas en la respuesta."""
        
        user_prompt = f"""Tengo {len(conceptos_data)} conceptos de pago y {len(operaciones_data)} operaciones.

        OPERACIONES:
        {json.dumps(operaciones_data, indent=2, ensure_ascii=False)}

        CONCEPTOS:
        {json.dumps(conceptos_data, indent=2, ensure_ascii=False)}

        Por favor, asocia cada concepto con la operación correspondiente basándote en los nombres de los intervinientes.
        Los nombres pueden estar escritos de forma diferente, así que busca similitudes y coincidencias.
        Devuelve SOLO el JSON con las asociaciones, sin texto adicional."""
        _logger.info(user_prompt)
        _logger.info(system_prompt)
        # Llamar al servicio de ChatGPT
        try:
            chatgpt_service = self.env['chatgpt.service']
            response = chatgpt_service.send_message(
                message=user_prompt,
                system_prompt=system_prompt,
                max_completion_tokens=4000
            )
            
            if not response.get('success'):
                raise UserError(_('Error al consultar IA: %s') % response.get('error', _('Error desconocido')))
            
            content = response.get('content', '').strip()
            if not content:
                raise UserError(_('La IA no devolvió ninguna respuesta.'))
            
            # Intentar extraer JSON de la respuesta (puede venir con markdown o texto adicional)
            json_content = content
            # Buscar JSON entre ```json ... ``` o ``` ... ```
            import re
            json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
            if json_match:
                json_content = json_match.group(1)
            else:
                # Buscar JSON directo
                json_match = re.search(r'\{.*"asociaciones".*\}', content, re.DOTALL)
                if json_match:
                    json_content = json_match.group(0)
            
            # Parsear JSON
            try:
                resultado = json.loads(json_content)
            except json.JSONDecodeError as e:
                _logger.error("Error parseando JSON de IA: %s. Contenido: %s", str(e), content[:500])
                raise UserError(_('La respuesta de la IA no es un JSON válido. Respuesta: %s') % content[:200])
            
            # Procesar asociaciones
            asociaciones = resultado.get('asociaciones', [])
            if not asociaciones:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Sin asociaciones'),
                        'message': _('La IA no encontró asociaciones válidas entre conceptos y operaciones.'),
                        'type': 'info',
                        'sticky': False,
                    }
                }
            
            # Crear diccionario de líneas por ID
            lineas_dict = {linea.id: linea for linea in count_lineas_pendientes}
            prestamos_dict = {prestamo.id: prestamo for prestamo in prestamos}
            
            asignadas = 0
            errores = []
            
            for asociacion in asociaciones:
                concepto_id = asociacion.get('concepto_id')
                operacion_id = asociacion.get('operacion_id')
                
                if not concepto_id or not operacion_id:
                    continue
                
                linea = lineas_dict.get(concepto_id)
                prestamo = prestamos_dict.get(operacion_id)
                
                if not linea:
                    errores.append(_('Línea con ID %s no encontrada') % concepto_id)
                    continue
                
                if not prestamo:
                    errores.append(_('Operación con ID %s no encontrada') % operacion_id)
                    continue
                
                # Asignar préstamo
                linea.write({
                    'prestamo_id': prestamo.id,
                    'auto_asignado': True
                })
                
                # Actualizar distribución automáticamente
                linea.actualiza_lista_distribucion()
                asignadas += 1
            
            mensaje = _('Se asignaron %s líneas usando IA.') % asignadas
            if errores:
                mensaje += '\n\nErrores:\n' + '\n'.join(errores[:5])
            
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Asociación con IA completada'),
                    'message': mensaje,
                    'type': 'success' if asignadas > 0 else 'warning',
                    'sticky': bool(errores),
                }
            }
            
        except UserError:
            raise
        except Exception as e:
            _logger.error("Error usando IA para asociar conceptos: %s", str(e), exc_info=True)
            raise UserError(_('Error al procesar con IA: %s') % str(e))

