#!/usr/bin/python
import os
import sys
from clang.cindex import *

# debug
import asciitree # must be version 0.2

# output
import jinja2

DEBUG_ATTRS = ['layoutData','topbarData','widgetData']

def node_children(node):
    l = list(c for c in node.get_children() if c is not None)
    if len(l) > 0:
        return l

    return []
    #decl = node.type.get_declaration()
    #return list(c for c in decl.get_children() if c is not None)

def print_node(node):
    text = node.spelling or node.displayname
    kind = str(node.kind)[str(node.kind).index('.')+1:]
    if CursorKind.FIELD_DECL == node.kind:
        size = node.type.get_size()
        return '{} {} {}'.format(kind, text, size)
    return '{} {}'.format(kind, text)

def dump_node(node):
    print(asciitree.draw_tree(node, node_children, print_node))

def print_error(*args):
    print("ERROR:",*args,file=sys.stderr)

def print_debug(*args):
    print("DBG:",*args,file=sys.stderr)

def bail_out(*args):
    print_error(*args)
    sys.exit(-1)

_anon_cnt = 0

def get_next_anon():
    global _anon_cnt
    label = str(_anon_cnt)
    _anon_cnt = _anon_cnt + 1
    return label

SIGNED_TYPES  = ['bool', 'char', 'short', 'int', 'long' ]

def map_type(type_name):
    if 'unsigned' in type_name:
        return 'unsigned'
    elif ('signed' in type_name) or (type_name in SIGNED_TYPES):
        return 'signed'

    return type_name

def mangle_type(type_name):
    return type_name.replace(':','_')

class AST_Element:
    def __init__(self, name, cursor):
        self.name   = name
        self.cursor = cursor
        self.elmts = []

    def get_children(self):
        return self.cursor.get_children()

    def get_elmts(self):
        return self.elmts

    def append(self,elmt):
        if isinstance(elmt, list):
            self.elmts.extend(elmt)
        else:
            self.elmts.append(elmt)

    def str(self):
        if hasattr(self,'value'):
            if hasattr(self,'ann'):
                return '{}:\t{} ({})'.format(self.name,self.value,self.ann)
            return '{}:\t{}'.format(self.name,self.value)
        elif hasattr(self,'type'):
            if hasattr(self,'var_type'):
                return '{} {}\t{}'.format(self.type, self.var_type, self.name)
            else:
                return '{}\t{}'.format(self.type, self.name)
        return self.name

class FieldAST(AST_Element):
    def __init__(self, name, cursor):
        super(FieldAST, self).__init__(name, cursor)

        self.is_array = False
        self.func = 'NULL'

        if self.name in DEBUG_ATTRS:
            #print("# field name={} type={} canon={} decl={}".format(self.name,cursor.type.spelling,cursor.type.get_canonical().spelling,str(cursor.type.kind)))
            #dump_node(cursor)
            pass

        if isinstance(cursor,Cursor):
            t = cursor.type
            if cursor.is_bitfield():
                self.bits = cursor.get_bitfield_width()
        else:
            # cursor is a type...
            t = cursor

        if not hasattr(self,'bits'):
            self.bits = t.get_size() * 8
            
        if t.kind == TypeKind.TYPEDEF:
            self.type = map_type(t.get_canonical().spelling)
        elif t.kind == TypeKind.CONSTANTARRAY:
            self.type = t.element_type.spelling
            self.is_array = True
            self.length = t.element_count

            if self.type == 'char':
                self.type   = 'string'
        else:
            self.type = map_type(t.spelling)


class StructAST(AST_Element):
    type = 'struct'

    def __init__(self, name, cursor, alt_name=''):
        if len(alt_name) > 0:
            name = alt_name

        if name == '':
            name = self.name_prefix() + 'anonymous_' + get_next_anon()
        else:
            name = self.name_prefix() + name

        super(StructAST, self).__init__(name, cursor)

    def name_prefix(self):
        return self.type + '_'

class UnionAST(StructAST):
    type = 'union'
        
class EnumAST(AST_Element):
    def __init__(self, name, cursor):
        super(EnumAST, self).__init__('enum_' + name, cursor)

class AST:
    name = 'ROOT'

    def __init__(self):
        self.structs = []
        self.enums = []

    def has_enum(self,name):
        for e in self.enums:
            if e.name == name:
                return True
        return False
        
    def get_enums(self):
        return self.enums

    def get_structs(self):
        return self.structs

    def has_struct(self,name):
        for s in self.structs:
            if s.name == name:
                return True
        return False

    def get_struct(self,name):
        for s in self.structs:
            if s.name == name:
                return s
        return None        

    def get_elmts(self):
        return self.enums + self.structs
    
    def append(self, elmt):
        if isinstance(elmt, StructAST):
            self.structs.append(elmt)
        elif isinstance(elmt, EnumAST):
            self.enums.append(elmt)

    def str(self):
        return self.name

def parse_struct(ast, node, alt_name):
    st = None

    if ast is not RootAST:
        ast.var_type = ast.type
        ast.type = 'struct'

    st = StructAST(node.spelling, node, alt_name)
    if (ast is RootAST):
        old_st = RootAST.get_struct(st.name)
        if old_st is not None:
            return old_st

    st.use_idx = False
    ann = get_annotations(node)
    if len(ann) > 0:
        #print(ann)
        for a in ann:
            if a['type'] == 'idx' and a['val'] == 'true':
                st.use_idx = True
                break

    for c in node.get_children():
        parse_field(st,c)

    if st is not ast:
        ast.append(st)

    #print_debug("IDX ",st.name, " ", st.use_idx)
    return st

def parse_union(ast, node):
    st = None

    if ast is not RootAST:
        ast.var_type = ast.type
        ast.type = 'union'

    st = UnionAST(node.spelling, node)
    if (ast is RootAST):
        old_st = RootAST.get_struct(st.name)
        if old_st is not None:
            return old_st

    for c in node.get_children():
        parse_field(st,c)

    if st is not ast:
        ast.append(st)

    return st

def get_annotations(node):
    l = list()
    for c in node.get_children():
        if c.kind == CursorKind.ANNOTATE_ATTR:
            ann = c.displayname.split(':')
            l.append({'type':ann[0], 'val':ann[1]})
    return l

def parse_field_record(f, node):
    alt_name = ''
    decl = node.type.get_declaration()

    if not decl.spelling == '':
        alt_name = mangle_type(node.type.spelling)
        #print("alt_name = " + alt_name)

    st = parse_node(RootAST,decl,alt_name)
    if st is not None:
        f.var_type = st.name
        f.type = st.type

def make_fake_array_struct(f, node_type, use_idx):

    field = FieldAST('val', node_type)
    type_name = field.type + '_' + str(field.bits)
    struct_name = 'struct_' + type_name

    if not RootAST.has_struct(struct_name):
        #print("# field created " + struct_name)
        st = StructAST(type_name, node_type)
        st.append(field)
        st.used_in_arrays = True
        st.use_idx = use_idx
        RootAST.append(st)

    f.type = 'array'
    f.var_type = struct_name
        
def parse_field_array(f, node):
    et = node.type.element_type
    if f.type != 'string':
        elmt_decl = et.get_declaration()

        use_idx = False
        ann = get_annotations(node)
        if len(ann) > 0:
            for a in ann:
                if a['type'] == 'idx':
                    use_idx = True

        # let's see first if it's some kind of struct/union/enum
        elmt_st = parse_node(RootAST, elmt_decl)
        if elmt_st is not None:
            f.type = 'array'
            f.var_type = elmt_st.name
            # mark array usage for unions
            elmt_st.used_in_arrays = True
        elif elmt_decl.kind == CursorKind.TYPEDEF_DECL:
            # it's some typedef
            make_fake_array_struct(f, elmt_decl, use_idx)
        elif et.kind == TypeKind.CONSTANTARRAY:
            # it's an array:
            #   let's create a fake struct with the element type
            make_fake_array_struct(f, et, use_idx)
        else:
            pass
            #print("# unknown array attr: {} {}".format(str(elmt_decl.kind), f.name))


def parse_field(ast,node):

    if (not node.is_anonymous()) and (node.kind != CursorKind.FIELD_DECL):
        return

    f = FieldAST(node.spelling, node)

    if node.type.kind in [TypeKind.ELABORATED, TypeKind.RECORD]:
        parse_field_record(f, node)

    elif node.type.kind == TypeKind.CONSTANTARRAY:
        parse_field_array(f, node)

    elif node.type.kind == TypeKind.ENUM:
        parse_node(root,node.type.get_declaration())

    ann = get_annotations(node)
    if len(ann) > 0:
        for a in ann:
            if a['type'] == 'enum':
                f.type = 'enum'
                enum_name = 'enum_' + a['val']
                f.var_type = enum_name
                if not RootAST.has_enum(enum_name):
                    parse_node(RootAST,get_top_node(a['val']))
            elif a['type'] == 'func':
                f.func = a['val']
            elif a['type'] == 'read':
                f.f_read = a['val']
            elif a['type'] == 'write':
                f.f_write = a['val']

    ast.append(f)


def parse_enum_field(ast,node):
    st = AST_Element(node.spelling[node.spelling.index('_')+1:], node)
    st.value = node.spelling #node.enum_value
    ast.append(st)
    # debug
    ann = get_annotations(node)
    if len(ann) > 0:
        #print(ann)
        st.ann = ann

def parse_enum(ast,node):
    st = EnumAST(node.spelling or node.displayname, node)
    for c in node.get_children():
        parse_enum_field(st,c)
    ast.append(st)

def parse_node(ast,node,alt_name=''):
    st = None

    if node.kind == CursorKind.STRUCT_DECL:
        st = parse_struct(ast,node,alt_name)
    elif node.kind == CursorKind.UNION_DECL:
        st = parse_union(ast,node)
    elif node.kind == CursorKind.FIELD_DECL:
        parse_field(ast,node)
    elif node.kind == CursorKind.ENUM_DECL:
        parse_enum(ast,node)
    else:
        pass

    return st

### main ###

def get_top_node(name):
    node = translation_unit.cursor
    for c in node.get_children():
        if c.spelling == name:
            # struct found
            return c

    bail_out(f"'{name}' not found")

def ast_children(ast_node):
    if ast_node is not None:
        return ast_node.get_elmts()
    return []

def print_ast_node(ast_node):
    #if isinstance(ast_node,dict):
    #    return str(ast_node)
    return ast_node.str()

if len(sys.argv) < 2:
    bail_out(f"usage: {sys.argv[0]} [header file name] [template] [node name] [additional compile args]")

CLANG_LIB_LOCATIONS = [
    '/usr/local/Cellar/llvm/6.0.0/lib/libclang.dylib',
    '/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/lib/libclang.dylib',
    '/Library/Developer/CommandLineTools/usr/lib/libclang.dylib',
    '/usr/lib/x86_64-linux-gnu/libclang-3.8.so.1'
]

# set clang lib file
for lib in CLANG_LIB_LOCATIONS:
    if os.path.exists(lib):
        Config.set_library_file(lib)
        break

# compile source file
index = Index.create()
translation_unit = index.parse(sys.argv[1], ['-x', 'c++', '-std=c++11'] + sys.argv[4:])

def show_tu_diags(diags, prefix=''):
    tu_errors =  0
    for diag in diags:
        tu_errors = tu_errors + 1
        print_error(prefix + str(diag))
        show_tu_diags(diag.children, '  ' + prefix)
    return tu_errors

tu_errors = show_tu_diags(translation_unit.diagnostics)
if tu_errors > 0:
    bail_out("{} error(s) while compiling '{}'".format(tu_errors, sys.argv[1]))

RootAST = AST()

top_node_names = sys.argv[3].split(',')
# cycle on top nodes:
for tn in top_node_names:
    top_node = get_top_node(tn)
    #dump_node(top_node)
    parse_node(RootAST, top_node)

#print("Enums:", RootAST.get_enums())
#print("Structs:", RootAST.get_structs())
#print(asciitree.draw_tree(RootAST, ast_children, print_ast_node))

#
# Template rendering
#

__max_str_len = 0

def max_len(str):
    global __max_str_len
    if len(str) > __max_str_len:
       __max_str_len = len(str)
    return str

def get_max_len():
    global __max_str_len
    return __max_str_len

def max_bits(struct):
    bits = 0
    for s in struct.get_elmts():
        if s.bits > bits:
            bits = s.bits
    return bits

template = jinja2.Template(open(sys.argv[2]).read(), lstrip_blocks=True, trim_blocks=True)

template.globals['max_len'] = max_len
template.globals['get_max_len'] = get_max_len
template.globals['max_bits'] = max_bits

## fixme: root_node_name needs to be mangled (contains ',')
print(template.render(root=RootAST,root_node_name=sys.argv[3]))
