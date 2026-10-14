"""Ready-made reports. Each is a report spec plus how to chart it."""


def _p(id, title, description, spec, chart='bar', group='Sales'):
    return {'id': id, 'title': title, 'description': description, 'group': group,
            'spec': {'dataset': 'work_orders', **spec}, 'chart': chart}


PRESETS = [
    _p('yearly_summary', 'Yearly summary', 'Jobs, sales, average ticket, discounting and turnaround by year.',
       {'dimensions': ['year'], 'metrics': ['jobs', 'sales', 'avg_ticket', 'discount_pct', 'avg_days_to_done']},
       'line'),
    _p('monthly_sales', 'Monthly sales', 'Sales and job count by month.',
       {'dimensions': ['month'], 'metrics': ['sales', 'jobs', 'avg_ticket']}, 'line'),
    _p('seasonality', 'Seasonality', 'Which months are busiest, all years combined.',
       {'dimensions': ['month_of_year'], 'metrics': ['jobs', 'sales']}),
    _p('weekday', 'Busiest weekdays', 'Orders taken by day of week.',
       {'dimensions': ['weekday'], 'metrics': ['jobs', 'sales']}),
    _p('ticket_size', 'Ticket size mix', 'How jobs split across price bands.',
       {'dimensions': ['price_band'], 'metrics': ['jobs', 'sales']}),
    _p('salesperson_year', 'Salesperson by year', 'Sales written by each salesperson per year.',
       {'dimensions': ['year', 'salesperson'], 'metrics': ['sales', 'jobs']}, 'bar', 'People'),
    _p('salesperson', 'Salesperson scorecard', 'All-time totals, ticket size and discounting per salesperson.',
       {'dimensions': ['salesperson'], 'metrics': ['jobs', 'sales', 'avg_ticket', 'discount_pct', 'mm_margin_pct']},
       'bar', 'People'),
    _p('top_customers', 'Top customers', 'Customers ranked by lifetime sales.',
       {'dimensions': ['customer_name', 'cust_no', 'customer_city'], 'metrics': ['sales', 'jobs', 'avg_ticket'],
        'limit': 50},
       'bar', 'Customers'),
    _p('new_vs_returning', 'New vs returning', 'Jobs from first-time vs repeat customers, by year.',
       {'dimensions': ['year', 'customer_type'], 'metrics': ['jobs', 'sales']}, 'bar', 'Customers'),
    _p('turnaround', 'Turnaround', 'How long jobs take from order to done.',
       {'dimensions': ['turnaround_band'], 'metrics': ['jobs'], 'date_from': '2023-01-01'}, 'bar', 'Operations'),
    _p('discounting', 'Discounting by year', 'Discount dollars and share of list price.',
       {'dimensions': ['year'], 'metrics': ['discounts', 'discount_pct']}, 'line'),
    _p('category_mix', 'Category mix by year', 'Moulding vs mat vs glazing vs labor revenue.',
       {'dimensions': ['year'], 'metrics': ['moulding_sales', 'mat_sales', 'glass_sales', 'labor_sales']}, 'line'),
    _p('vendors', 'Moulding vendors', 'Moulding sales, cost and margin by vendor.',
       {'dataset': 'components', 'dimensions': ['comp_vendor'], 'metrics': ['lines', 'net_sales', 'cost', 'margin_pct'],
        'filters': [{'field': 'comp_kind', 'op': 'in', 'value': ['Moulding']}]}, 'bar', 'Products'),
    _p('top_mouldings', 'Top mouldings', 'Most-used moulding profiles.',
       {'dataset': 'components', 'dimensions': ['comp_name', 'comp_vendor'],
        'metrics': ['lines', 'feet', 'net_sales', 'margin_pct'],
        'filters': [{'field': 'comp_kind', 'op': 'in', 'value': ['Moulding']}], 'limit': 50}, 'bar', 'Products'),
    _p('top_mats', 'Top mats', 'Most-used mat boards.',
       {'dataset': 'components', 'dimensions': ['comp_name', 'comp_vendor'], 'metrics': ['lines', 'net_sales'],
        'filters': [{'field': 'comp_kind', 'op': 'in', 'value': ['Mat']}], 'limit': 50}, 'bar', 'Products'),
    _p('glazing_mix', 'Glazing mix', 'Which glass/acrylic customers choose.',
       {'dataset': 'components', 'dimensions': ['comp_name'], 'metrics': ['lines', 'net_sales', 'avg_price'],
        'filters': [{'field': 'comp_kind', 'op': 'in', 'value': ['Glazing']}]}, 'bar', 'Products'),
    _p('component_margin', 'Margin by component type', 'Margin on the components LifeSaver tracks cost for.',
       {'dataset': 'components', 'dimensions': ['comp_kind'], 'metrics': ['net_sales', 'cost', 'margin', 'margin_pct'],
        'filters': [{'field': 'comp_kind', 'op': 'in', 'value': ['Moulding', 'Mat', 'Fillet']}]},
       'bar', 'Products'),
]

BY_ID = {p['id']: p for p in PRESETS}
