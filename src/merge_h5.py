import numpy as np
import tables

from data import copyTable, mergeArrays, get_fields, index_table_light 
from utils import timed, loop_wh_progress

__version__ = "0.2"

def get_h5_fields(input_file):
    return dict((table._v_name, get_fields(table)) 
                for table in input_file.iterNodes(input_file.root.entities))

def merge_fields(fields1, fields2):
    names1 = set(name for name, _ in fields1)
    names2 = set(name for name, _ in fields2)
    names_notin1 = names2 - names1
    fields_notin1 = [(name, type_) for name, type_ in fields2 
                     if name in names_notin1]
    return fields1 + fields_notin1

def merge_h5(input1_path, input2_path, output_path):        
    input1_file = tables.openFile(input1_path, mode="r")
    input2_file = tables.openFile(input2_path, mode="r")
    
    output_file = tables.openFile(output_path, mode="w")
    output_globals = output_file.createGroup("/", "globals", "Globals")

    print "copying globals from", input1_path,
    copyTable(input1_file.root.globals.periodic, output_file, output_globals)
    print "done."
    
    input1_entities = input1_file.root.entities
    input2_entities = input2_file.root.entities
    
    fields1 = get_h5_fields(input1_file)
    fields2 = get_h5_fields(input2_file)

    ent_names1 = set(fields1.keys())
    ent_names2 = set(fields2.keys())
    
    output_entities = output_file.createGroup("/", "entities", "Entities")
    for ent_name in sorted(ent_names1 | ent_names2):
        print
        print ent_name
        ent_fields1 = fields1.get(ent_name, [])
        ent_fields2 = fields2.get(ent_name, [])
        output_fields = merge_fields(ent_fields1, ent_fields2)
        output_table = output_file.createTable(output_entities, ent_name, 
                                               np.dtype(output_fields))
        
        if ent_name in ent_names1:
            table1 = getattr(input1_entities, ent_name)
            print " * indexing table from %s ..." % input1_path,
            input1_rows = index_table_light(table1)
            print "done."
        else:
            table1 = None
            input1_rows = {}
            
        if ent_name in ent_names2:
            table2 = getattr(input2_entities, ent_name)
            print " * indexing table from %s ..." % input2_path,
            input2_rows = index_table_light(table2)
            print "done."
        else:
            table2 = None
            input2_rows = {}

        print " * merging: ",        
        input1_periods = input1_rows.keys()
        input2_periods = input2_rows.keys()
        output_periods = sorted(set(input1_periods) | set(input2_periods))
        def merge_period(period_idx, period): 
            if ent_name in ent_names1:
                start, stop = input1_rows.get(period, (0, 0))
                input1_array = table1.read(start, stop)
            else:
                input1_array = None

            if ent_name in ent_names2:
                start, stop = input2_rows.get(period, (0, 0))
                input2_array = table2.read(start, stop)
            else:
                input2_array = None
                
            if ent_name in ent_names1 and ent_name in ent_names2:
                output_array, _ = mergeArrays(input1_array, input2_array)
            elif ent_name in ent_names1:
                output_array = input1_array
            elif ent_name in ent_names2:
                output_array = input2_array
            else:
                raise Exception("this shouldn't have happened")
            output_table.append(output_array)
            output_table.flush()
        loop_wh_progress(merge_period, output_periods)
        print " done."       

    input1_file.close()
    input2_file.close()
    output_file.close()


if __name__ == '__main__':
    import sys, platform

    print "LIAM HDF5 merge %s using Python %s (%s)\n" % \
          (__version__, platform.python_version(), platform.architecture()[0])

    args = sys.argv
    if len(args) < 4:
        print "Usage: %s inputpath1 inputpath2 outputpath" % args[0]
        sys.exit()
    
    timed(merge_h5, args[1], args[2], args[3])
