# -*- coding: utf-8 -*-
{
    'name': 'Extractos Bancarios',
    'version': '17.0.1.0.0',
    'category': 'Finance',
    'summary': 'Gestión de extractos bancarios e importación de pagos',
    'description': """
        Módulo para gestionar extractos bancarios de diferentes formatos
        y automatizar la importación y distribución de pagos a préstamos.
        
        Características:
        - Configuración de tipos de extracto por banco
        - Gestión de carteras (prestamista + tipo de extracto)
        - Importación automática de extractos
        - Asignación automática de préstamos
        - Distribución automática de pagos
    """,
    'author': 'TopGlobal',
    'website': 'https://www.topglobal.es',
    'license': 'LGPL-3',
    'depends': [
        'base',
        'linx',
        'contacts',
        'odoo_ia',
    ],
    'data': [
        'security/ir.model.access.csv',
        'views/tipo_extracto_views.xml',
        'views/cartera_views.xml',
        'views/extracto_views.xml',
        'views/extracto_linea_views.xml',
        'views/menu_views.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
}

