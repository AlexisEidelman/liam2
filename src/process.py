from __future__ import division, print_function

import collections

import numpy as np

import config
from diff_h5 import diff_array
from data import append_carray_to_table, ColumnArray
from expr import Expr, Variable, type_to_idx, idx_to_type, expr_eval, expr_cache, \
    BinaryOp
from context import EntityContext
import utils


class BreakpointException(Exception):
    pass


class Process(object):
    def __init__(self):
        self.name = None
        self.entity = None

    def attach(self, name, entity):
        self.name = name
        self.entity = entity

    def run_guarded(self, context):
        try:
            # purge extra
            context.entity_data.extra = {}
            self.run(context)
        except BreakpointException:
            #XXX: store this in the (evaluation) context instead?
            context.simulation.stepbystep = True

    def run(self, context):
        raise NotImplementedError()

    def expressions(self):
        raise NotImplementedError()

    def __str__(self):
        return "<process '%s'>" % self.name


class Compute(Process):
    """these processes only compute an expression and do not store their
       result (but they usually have side-effects). No class inherits from
       this but we use it when a user does not store anywhere the result of
       an expression (with a side effect) which *does* return a value.
       new() is a good example for this"""

    def __init__(self, expr):
        super(Compute, self).__init__()
        self.expr = expr

    def run(self, context):
        expr_eval(self.expr, context)

    def expressions(self):
        if isinstance(self.expr, Expr):
            yield self.expr


class Assignment(Process):
    def __init__(self, expr):
        super(Assignment, self).__init__()
        self.expr = expr
        self.temporary = True

    def attach(self, name, entity):
        super(Assignment, self).attach(name, entity)
        if self.name is None:
            raise Exception('trying to store None key')
        self.temporary = name not in entity.stored_fields

    def run(self, context):
        value = expr_eval(self.expr, context)
        self.store_result(value)

        period = context.period
        if isinstance(period, np.ndarray):
            assert np.isscalar(period) or not period.shape
            period = int(period)
        cache_key = (Variable(self.name), period, context.entity_name,
                     context.filter_expr)
        if self.expr == BinaryOp('+', Variable('age'), 1):
            print('!!! removing dirty cache for', cache_key)

        if cache_key in expr_cache:
            print('!!! removing dirty cache for', cache_key)
            del expr_cache[cache_key]
        # expr_cache.pop(cache_key, None)

    def store_result(self, result):
        if result is None:
            return

        if isinstance(result, np.ndarray):
            res_type = result.dtype.type
        else:
            res_type = type(result)

        if self.temporary:
            target = self.entity.temp_variables
        else:
            # we cannot store/cache self.entity.array[self.name] because the
            # array object can change (eg when enlarging it due to births)
            target = self.entity.array

            #TODO: assert type for temporary variables too
            target_type_idx = type_to_idx[target[self.name].dtype.type]
            res_type_idx = type_to_idx[res_type]
            if res_type_idx > target_type_idx:
                raise Exception(
                    "trying to store %s value into '%s' field which is of "
                    "type %s" % (idx_to_type[res_type_idx].__name__,
                                 self.name,
                                 idx_to_type[target_type_idx].__name__))

        # the whole column is updated
        target[self.name] = result

    def expressions(self):
        if isinstance(self.expr, Expr):
            yield self.expr


class While(Process):
    """this class implements while loops"""

    def __init__(self, cond, code):
        """
        cond -- an Expr returning a (single) boolean, it means the condition
                value must be the same for all individuals
        code -- a ProcessGroup
        """
        Process.__init__(self)
        self.cond = cond
        assert isinstance(code, ProcessGroup)
        self.code = code

    def attach(self, name, entity):
        Process.attach(self, name, entity)
        self.code.attach('while:code', entity)

    def run_guarded(self, context):
        while True:
            cond_value = expr_eval(self.cond, context)
            if not cond_value:
                break

            self.code.run_guarded(context)
            #FIXME: this is a bit brutal :) This is necessary because
            # otherwise test_while loops indefinitely (because "values" is
            # never incremented)
            expr_cache.clear()

    def expressions(self):
        if isinstance(self.cond, Expr):
            yield self.cond
        for e in self.code.expressions():
            yield e


#TODO: I think I can kill this class by moving the methods to Function
class AbstractProcessGroup(Process):
    def backup_and_purge_locals(self):
        # backup and purge local variables
        backup = {}
        for name in self.entity.local_var_names:
            backup[name] = self.entity.temp_variables.pop(name)
        return backup

    def purge_and_restore_locals(self, backup):
        # purge the local from the function we just ran
        self.entity.purge_locals()
        # restore local variables for our caller
        for k, v in backup.iteritems():
            self.entity.temp_variables[k] = v


class ProcessGroup(AbstractProcessGroup):
    def __init__(self, name, subprocesses, purge=True):
        super(ProcessGroup, self).__init__()
        self.name = name
        self.subprocesses = subprocesses
        self.calls = collections.Counter()
        self.purge = purge
        self.versions = {}

    def attach(self, name, entity):
        assert name == self.name
        Process.attach(self, name, entity)
        for k, v in self.subprocesses:
            v.attach(k, entity)

    def run_guarded(self, context):
        period = context.period

        print()
        for k, v in self.subprocesses:
            print("    *", end=' ')
            if k is not None:
                print(k, end=' ')
            utils.timed(v.run_guarded, context)
#            print "done."
            context.simulation.start_console(context)
        if config.autodump is not None:
            self._autodump(period)

        if config.autodiff is not None:
            self._autodiff(period)

        if self.purge:
            self.entity.purge_locals()

    @property
    def predictors(self):
        return [v.name for _, v in self.subprocesses
                if isinstance(v, Assignment)]

    @property
    def _modified_fields(self):
        fnames = self.predictors
        if not fnames:
            return []

        fnames.insert(0, 'id')
        temp = self.entity.temp_variables
        array = self.entity.array
        length = len(array)

        fields = [(k, temp[k] if k in temp else array[k])
                  for k in utils.unique(fnames)]
        return [(k, v) for k, v in fields
                if isinstance(v, np.ndarray) and v.shape == (length,)]

    def _tablename(self, period):
        self.calls[(period, self.name)] += 1
        num_calls = self.calls[(period, self.name)]
        if num_calls > 1:
            return '{}_{}'.format(self.name, num_calls)
        else:
            return self.name

    def _autodump(self, context):
        fields = self._modified_fields
        if not fields:
            return

        period = context.period
        fname, numrows = config.autodump
        h5file = config.autodump_file
        name = self._tablename(period)
        dtype = np.dtype([(k, v.dtype) for k, v in fields])
        table = h5file.createTable('/{}'.format(period), name, dtype,
                                   createparents=True)

        fnames = [k for k, _ in fields]
        print("writing {} to {}/{}/{} ...".format(', '.join(fnames),
                                                  fname, period, name))

        entity_context = EntityContext(self.entity, {'period': period})
        append_carray_to_table(entity_context, table, numrows)
        print("done.")

    def _autodiff(self, period, numdiff=10, raiseondiff=False):
        fields = self._modified_fields
        if not fields:
            return

        fname, numrows = config.autodiff
        h5file = config.autodump_file
        tablepath = '/{}/{}'.format(period, self._tablename(period))
        print("comparing with {}{} ...".format(fname, tablepath))
        if tablepath in h5file:
            table = h5file.getNode(tablepath)
            disk_array = ColumnArray.from_table(table, stop=numrows)
            diff_array(disk_array, ColumnArray(fields), numdiff, raiseondiff)
        else:
            print("  SKIPPED (could not find table)")

    def expressions(self):
        for _, p in self.subprocesses:
            for e in p.expressions():
                yield e

    def ssa(self):
        procedure_vars = set(k for k, p in self.subprocesses if k is not None)
        global_vars = set(self.entity.variables.keys())
        local_vars = procedure_vars - global_vars

        from collections import defaultdict
        self.versions = defaultdict(int)
        for k, p in self.subprocesses:
            for expr in p.expressions():
                for node in expr.all_of(Variable):
                    if node.name not in local_vars:
                        continue
                    node.version = self.versions[node.name]
            if isinstance(p, Assignment):
                # is this always == k?
                target = p.name
                if target not in local_vars:
                    continue
                version = self.versions[target]
                print("%s version %d" % (target, version))
                self.versions[target] = version + 1


class Function(AbstractProcessGroup):
    """this class implements user-defined functions"""

    def __init__(self, argnames, code=None, result=None):
        """
        args -- a list of strings
        code -- a ProcessGroup (or None)
        result -- an Expr (or None)
        """
        Process.__init__(self)

        assert isinstance(argnames, list)
        assert all(isinstance(a, basestring) for a in argnames)
        self.argnames = argnames

        assert code is None or isinstance(code, ProcessGroup)
        self.code = code

        assert result is None or isinstance(result, Expr)
        self.result = result

    def attach(self, name, entity):
        Process.attach(self, name, entity)
        self.code.attach('func:code', entity)

    def run_guarded(self, context, *args, **kwargs):
        #XXX: wouldn't some form of cascading context make all this junk much
        # cleaner? Context(globalvars, localvars) (globalvars contain both
        # entity fields and global temporaries)

        backup = self.backup_and_purge_locals()

        if len(args) != len(self.argnames):
            print(self.argnames)
            raise TypeError("takes exactly %d arguments (%d given)" %
                            (len(self.argnames), len(args)))

        context = context.copy()
        # add arguments to the local namespace
        for name, value in zip(self.argnames, args):
            # backup the variable if it existed in the caller namespace
            # if name in self.entity.temp_variables:
            #     backup[name] = self.entity.temp_variables.pop(name)
            # self.entity.temp_variables[name] = value
            context[name] = value
        self.code.run_guarded(context)
        result = expr_eval(self.result, context)

        self.purge_and_restore_locals(backup)
        return result

    def expressions(self):
        #XXX: not sure what to put here as I don't remember what it is used for
        for e in self.code.expressions():
            yield e
