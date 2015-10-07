# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution
#    Copyright (C) 2013-2015 CodUP (<http://codup.com>).
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################

import time
import logging

from openerp.osv import fields, osv
from openerp.tools import DEFAULT_SERVER_DATETIME_FORMAT as DATETIME_FMT

from .helpers import timegm, DAY
_logger = logging.getLogger(__name__)


def find_step(start, end, tmin, tmax):
    """Try to find a step size in the interval [tmin, tmax] such that the interval
    [start, end] can be evenly divided in steps of that size, in a number of
    intervals close to the average of tmin and tmax.

    """
    nb_steps = round(2*(end - start)/(tmin + tmax), 0)
    if nb_steps != 0:
        step = (end - start)/nb_steps
        if step < tmin:
            nb_steps -= 1
            if nb_steps != 0:
                step = (end - start) / nb_steps
            if step < tmin or step > tmax:
                step = tmin
        elif step > tmax:
            nb_steps += 1
            step = (end - start) / nb_steps
            if step < tmin or step > tmax:
                step = tmax
    else:
        step = tmin
    return step


class mro_order(osv.osv):
    _inherit = 'mro.order'
    
    MAINTENANCE_TYPE_SELECTION = [
        ('bm', 'Breakdown'),
        ('cm', 'Corrective'),
        ('pm', 'Preventive')
    ]
    
    _columns = {
        'maintenance_type': fields.selection(MAINTENANCE_TYPE_SELECTION, 'Maintenance Type', required=True, readonly=True, states={'draft': [('readonly', False)]}),
    }
    

    def replan_pm(self, cr, uid, context=None):
        rule_obj = self.pool.get('mro.pm.rule')
        asset_obj = self.pool.get('asset.asset')
        ids = rule_obj.search(cr, uid, [])
        for rule in rule_obj.browse(cr,uid,ids,context=context):
            tasks = [x for x in rule.pm_rules_line_ids]
            if not len(tasks):
                continue
            horizon = rule.horizon
            origin = rule.name
            for asset in rule.category_id.asset_ids:
                for meter in asset.meter_ids:
                    if meter.name != rule.parameter_id or meter.state != 'reading':
                        continue
                    self.planning_strategy_1(cr, uid, asset, meter, tasks, horizon, origin, context=context)
        return True

    def planning_strategy_1(self, cr, uid, asset, meter, tasks, horizon, origin, context=None):
        meter_obj = self.pool.get('mro.pm.meter')
        tasks.sort(lambda y,x: cmp(x.meter_interval_id.interval_max, y.meter_interval_id.interval_max))
        nb_tasks = len(tasks)
        task_ids = []
        meter_daily_increase = []
        interval_min = []
        interval_max = []
        steps = []
        date_min = []
        date_max = []
        date_opt = []
        for task in tasks:
            task_ids.append(task.task_id.id)
            order_ids = self.search(cr, uid,
                [('asset_id', '=', asset.id),
                ('state', 'not in', ('draft','cancel')),
                ('maintenance_type', '=', 'pm'),
                ('task_id', 'in', task_ids)],
                limit=1,
                order='date_execution desc')
            if order_ids:
                date = self.browse(cr, uid, order_ids[0], context=context).date_execution
                meter_daily_increase.append(DAY*meter_obj.get_reading(cr, uid, meter.id, date))
            else:
                meter_daily_increase.append(0)
            interval_min.append(DAY*task.meter_interval_id.interval_min)
            interval_max.append(DAY*task.meter_interval_id.interval_max)
            steps.append(0)
            date_min.append(0)
            date_max.append(0)
            date_opt.append(0)
        meter_latest_tot_val = DAY*meter.total_value
        latest_date_reading = meter.date_timegm
        utilization_rate = meter.utilization
        planning_horizon = DAY * 31 * horizon
        now = timegm()
        steps[0] = interval_min[0]
        for i in range(1, nb_tasks):
            steps[i] = find_step(meter_daily_increase[i],
                                   meter_daily_increase[i-1] + steps[i-1],
                                   interval_min[i],
                                   interval_max[i])
        for i in range(nb_tasks):
            date_min[i] = latest_date_reading + (interval_min[i] - meter_latest_tot_val + meter_daily_increase[i])/utilization_rate
            date_max[i] = date_min[i] + (interval_max[i] - interval_min[i])/utilization_rate
            date_opt[i] = latest_date_reading + (steps[i] - meter_latest_tot_val + meter_daily_increase[i])/utilization_rate
        current_date = date_opt[-1]
        if nb_tasks > 1:
            current_date = min(current_date, min(date_max[:-1]))
        current_date = max(current_date, now)
        current_meter_tot_val = meter_latest_tot_val + (current_date - latest_date_reading) * utilization_rate
        delta = current_meter_tot_val - meter_daily_increase[-1]
        order_ids = self.search(cr, uid, 
            [('asset_id', '=', asset.id),
            ('state', '=', 'draft'),
            ('maintenance_type', '=', 'pm'),
            ('task_id', 'in', task_ids)],
            order='date_execution')
        print "draft order_ids", order_ids
        for order in self.browse(cr, uid, order_ids, context=context):
            current_date_str = time.strftime(DATETIME_FMT, time.gmtime(current_date))
            values = {
                'date_planned': current_date_str,
                'date_scheduled': current_date_str,
                'date_execution': current_date_str,
                'origin': origin,
                'state': 'draft',
                'maintenance_type': 'pm',
                'asset_id': asset.id,
            }
            task = tasks[-1].task_id
            meter_daily_increase[-1] = current_meter_tot_val
            steps[-1] = find_step(meter_daily_increase[-1],
                                  meter_daily_increase[nb_tasks-2] + steps[nb_tasks-2],
                                  interval_min[-1],
                                  interval_max[-1])
            for i in range(nb_tasks-1):
                if date_min[i] < current_date + (steps[-1]-interval_max[i]+interval_min[i])/utilization_rate:
                    task = tasks[i].task_id
                    for j in range(i, nb_tasks-1):
                        meter_daily_increase[j] = current_meter_tot_val
                    for j in range(i, nb_tasks-1):
                        steps[j] = find_step(meter_daily_increase[j],
                                               meter_daily_increase[j-1] + steps[j-1],
                                               interval_min[j],
                                               interval_max[j])
                        date_min[j] = current_date + interval_min[j]/utilization_rate
                        date_max[j] = current_date + interval_max[j]/utilization_rate
                        date_opt[j] = current_date + steps[j]/utilization_rate
                    break
            steps[-1] = find_step(meter_daily_increase[-1],
                                  meter_daily_increase[nb_tasks-2] + steps[nb_tasks-2],
                                  interval_min[-1],
                                  interval_max[-1])
            date_min[-1] = current_date + interval_min[-1]/utilization_rate
            date_max[-1] = current_date + interval_max[-1]/utilization_rate
            date_opt[-1] = current_date + steps[-1]/utilization_rate
            values.update({'task_id': task.id,
                           'description': task.name,
                           'tools_description': task.tools_description,
                           'labor_description': task.labor_description,
                           'operations_description': task.operations_description,
                           'documentation_description': task.documentation_description,
                           })
            parts_lines = [[2,line.id] for line in order.parts_lines]
            for line in task.parts_lines:
                parts_lines.append([0,0,{
                    'name': line.name,
                    'parts_id': line.parts_id.id,
                    'parts_qty': line.parts_qty,
                    'parts_uom': line.parts_uom.id,
                    }])
            values['parts_lines'] = parts_lines
            self.write(cr, uid, [order.id], values)
            current_date = date_opt[-1]
            if nb_tasks > 1:
                current_date = min(current_date, min(date_max[:-1]))
            old_tot_val = current_meter_tot_val
            current_meter_tot_val = meter_latest_tot_val + (current_date - latest_date_reading)*utilization_rate
            delta = current_meter_tot_val - old_tot_val

        planning_end_date = now + planning_horizon
        while current_date < planning_end_date:
            Tp = time.strftime(DATETIME_FMT, time.gmtime(current_date))
            values = {
                'date_planned': Tp,
                'date_scheduled': Tp,
                'date_execution': Tp,
                'origin': origin,
                'state': 'draft',
                'maintenance_type': 'pm',
                'asset_id': asset.id,
            }
            task = tasks[-1].task_id
            meter_daily_increase[-1] = current_meter_tot_val
            steps[-1] = find_step(meter_daily_increase[-1],
                                  meter_daily_increase[nb_tasks-2] + steps[nb_tasks-2],
                                  interval_min[-1],
                                  interval_max[-1])
            for i in range(nb_tasks-1):
                if date_min[i] < current_date + (steps[-1]-interval_max[i]+interval_min[i])/utilization_rate:
                    task = tasks[i].task_id
                    for j in range(i, nb_tasks-1):
                        meter_daily_increase[j] = current_meter_tot_val
                    for j in range(i, nb_tasks-1):
                        steps[j] = find_step(meter_daily_increase[j],
                                               meter_daily_increase[j-1] + steps[j-1],
                                               interval_min[j],
                                               interval_max[j])
                        date_min[j] = current_date + interval_min[j]/utilization_rate
                        date_max[j] = current_date + interval_max[j]/utilization_rate
                        date_opt[j] = current_date + steps[j]/utilization_rate
                    break
            steps[-1] = find_step(meter_daily_increase[-1],
                                  meter_daily_increase[nb_tasks-2] + steps[nb_tasks-2],
                                  interval_min[-1],
                                  interval_max[-1])
            date_min[-1] = current_date + interval_min[-1] / utilization_rate
            date_max[-1] = current_date + interval_max[-1] / utilization_rate
            date_opt[-1] = current_date + steps[-1] / utilization_rate
            values.update({'task_id': task.id,
                           'description': task.name,
                           'tools_description': task.tools_description,
                           'labor_description': task.labor_description,
                           'operations_description': task.operations_description,
                           'documentation_description': task.documentation_description,
                           })
            parts_lines = []
            for line in task.parts_lines:
                parts_lines.append([0,0,{
                    'name': line.name,
                    'parts_id': line.parts_id.id,
                    'parts_qty': line.parts_qty,
                    'parts_uom': line.parts_uom.id,
                    }])
            values['parts_lines'] = parts_lines
            self.create(cr, uid, values)
            current_date = date_opt[-1]
            if nb_tasks > 1:
                current_date = min(current_date, min(date_max[:-1]))
            old_tot_val = current_meter_tot_val
            current_meter_tot_val = meter_latest_tot_val + (current_date - latest_date_reading) * utilization_rate
            delta = current_meter_tot_val - old_tot_val
        return True


class mro_task(osv.osv):
    _inherit = 'mro.task'
    
    MAINTENANCE_TYPE_SELECTION = [
        ('cm', 'Corrective'),
        ('pm', 'Preventive')
    ]
    
    _columns = {
        'maintenance_type': fields.selection(MAINTENANCE_TYPE_SELECTION, 'Maintenance Type', required=True),
    }

    
# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:
