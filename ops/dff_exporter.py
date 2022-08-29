# GTA DragonFF - Blender scripts to edit basic GTA formats
# Copyright (C) 2019  Parik

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

import bpy
import bmesh
import mathutils
import os
import os.path
from collections import defaultdict

from ..gtaLib import dff
from .col_exporter import export_col

#######################################################
def clear_extension(string):
    
    k = string.rfind('.')
    return string if k < 0 else string[:k]
    
#######################################################
class material_helper:

    """ Material Helper for Blender 2.7x and 2.8 compatibility"""

    #######################################################
    def get_base_color(self):

        if self.principled:
            node = self.principled.node_principled_bsdf.inputs["Base Color"]
            return dff.RGBA._make(
                list(int(255 * x) for x in node.default_value)
            )
        alpha = int(self.material.alpha * 255)
        return dff.RGBA._make(
                    list(int(255*x) for x in self.material.diffuse_color) + [alpha]
                )

    #######################################################
    def get_texture(self):

        texture = dff.Texture()
        texture.filters = 0 # <-- find a way to store this in Blender
        
        # 2.8         
        if self.principled:
            if self.principled.base_color_texture.image is not None:

                node_label = self.principled.base_color_texture.node_image.label
                image_name = self.principled.base_color_texture.image.name

                # Use node label if it is a substring of image name, else
                # use image name
                
                texture.name = clear_extension(
                    node_label
                    if node_label in image_name and node_label != ""
                    else image_name
                )
                return texture
            return None

        # Blender Internal
        try:
            texture.name = clear_extension(
                self.material.texture_slots[0].texture.image.name
            )
            return texture

        except BaseException:
            return None

    #######################################################
    def get_surface_properties(self):

        if self.principled:
            specular = self.principled.specular
            diffuse = self.principled.roughness
            ambient = self.material.dff.ambient
            
        else:

            specular = self.material.specular_intensity
            diffuse  = self.material.diffuse_intensity
            ambient  = self.material.ambient
            
        return dff.GeomSurfPro(ambient, specular, diffuse)

    #######################################################
    def get_normal_map(self):

        bump_texture = None
        height_texture = dff.Texture()

        if not self.material.dff.export_bump_map:
            return None
        
        # 2.8
        if self.principled:
            
            if self.principled.normalmap_texture.image is not None:

                bump_texture = dff.Texture()
                
                node_label = self.principled.node_normalmap.label
                image_name = self.principled.normalmap_texture.image.name

                bump_texture.name = clear_extension(
                    node_label
                    if node_label in image_name and node_label != ""
                    else image_name
                )
                intensity = self.principled.normalmap_strength

        height_texture.name = self.material.dff.bump_map_tex
        if height_texture.name == "":
            height_texture = None

        if bump_texture is not None:
            return dff.BumpMapFX(intensity, height_texture, bump_texture)

        return None

    #######################################################
    def get_environment_map(self):

        if not self.material.dff.export_env_map:
            return None

        texture_name = self.material.dff.env_map_tex
        coef         = self.material.dff.env_map_coef
        use_fb_alpha  = self.material.dff.env_map_fb_alpha

        texture = dff.Texture()
        texture.name = texture_name
        texture.filters = 0
        
        return dff.EnvMapFX(coef, use_fb_alpha, texture)

    #######################################################
    def get_specular_material(self):

        props = self.material.dff
        
        if not props.export_specular:
            return None

        return dff.SpecularMat(props.specular_level,
                               props.specular_texture.encode('ascii'))

    #######################################################
    def get_reflection_material(self):

        props = self.material.dff

        if not props.export_reflection:
            return None

        return dff.ReflMat(
            props.reflection_scale_x, props.reflection_scale_y,
            props.reflection_offset_x, props.reflection_offset_y,
            props.reflection_intensity
        )

    #######################################################
    def get_user_data(self):

        if 'dff_user_data' not in self.material:
            return None
        
        return dff.UserData.from_mem(
                self.material['dff_user_data'])
    
    #######################################################
    def get_uv_animation(self):

        #TODO: Add Blender Internal Support

        anim = dff.UVAnim()

        # See if export_animation checkbox is checked
        if not self.material.dff.export_animation:
            return None

        anim.name = self.material.dff.animation_name
        
        if self.principled:
            if self.principled.base_color_texture.has_mapping_node():
                anim_data = self.material.node_tree.animation_data
                
                fps = bpy.context.scene.render.fps
                
                if anim_data:
                    for curve in anim_data.action.fcurves:

                        # Rw doesn't support Z texture coordinate.
                        if curve.array_index > 1:
                            continue

                        # Offset in the UV array
                        uv_offset = {
                            'nodes["Mapping"].inputs[1].default_value': 4,
                            'nodes["Mapping"].inputs[3].default_value': 1,
                        }

                        if curve.data_path not in uv_offset:
                            continue
                        
                        off = uv_offset[curve.data_path]
                        
                        for i, frame in enumerate(curve.keyframe_points):
                            
                            if len(anim.frames) <= i:
                                anim.frames.append(dff.UVFrame(0,[0]*6, i-1))

                            _frame = list(anim.frames[i])
                                
                            uv = _frame[1]
                            uv[off + curve.array_index] = frame.co[1]

                            _frame[0] = frame.co[0] / fps

                            anim.frames[i] = dff.UVFrame._make(_frame)
                            anim.duration = max(anim.frames[i].time,anim.duration)
                            
                    return anim
    
    #######################################################
    def __init__(self, material):
        self.material = material
        self.principled = None

        if bpy.app.version >= (2, 80, 0):
            from bpy_extras.node_shader_utils import PrincipledBSDFWrapper
            
            self.principled = PrincipledBSDFWrapper(self.material,
                                                    is_readonly=False)
        
        

#######################################################
def edit_bone_matrix(edit_bone):

    """ A helper function to return correct matrix from any
        bone setup there might. 
        
        Basically resets the Tail to +0.05 in Y Axis to make a correct
        prediction
    """

    return edit_bone.matrix
    
    # What I wrote above is rubbish, by the way. This is a hack-ish solution
    original_tail = list(edit_bone.tail)
    edit_bone.tail = edit_bone.head + mathutils.Vector([0, 0.05, 0])
    matrix = edit_bone.matrix

    edit_bone.tail = original_tail
    return matrix
            
#######################################################
class dff_exporter:

    selected = False
    mass_export = False
    file_name = ""
    dff = None
    version = None
    current_clump = None
    clumps = {}
    frames = {}
    bones = {}
    parent_queue = {}
    collection = None
    export_coll = False

    #######################################################
    @staticmethod
    def multiply_matrix(a, b):
        # For compatibility with 2.79
        if bpy.app.version < (2, 80, 0):
            return a * b
        return a @ b
    
    #######################################################
    @staticmethod
    def create_frame(obj, append=True, set_parent=True):
        self = dff_exporter
        
        frame       = dff.Frame()
        frame_index = len(self.current_clump.frame_list)
        
        # Get rid of everything before the last period
        frame.name = clear_extension(obj.name)

        # Is obj a bone?
        is_bone = type(obj) is bpy.types.Bone

        # Scan parent queue
        for name in self.parent_queue:
            if name == obj.name:
                index = self.parent_queue[name]
                self.current_clump.frame_list[index].parent = frame_index
        
        matrix                = obj.matrix_local
        frame.creation_flags  =  0
        frame.parent          = -1
        frame.position        = matrix.to_translation()
        frame.rotation_matrix = dff.Matrix._make(
            matrix.to_3x3().transposed()
        )

        if "dff_user_data" in obj:
            frame.user_data = dff.UserData.from_mem(obj["dff_user_data"])

        id_array = self.bones if is_bone else self.frames
        
        if set_parent and obj.parent is not None:
            frame.parent = id_array[obj.parent.name]            
            

        id_array[obj.name] = frame_index

        if append:
            self.current_clump.frame_list.append(frame)

        return frame

    #######################################################
    @staticmethod
    def generate_material_list(obj):
        materials = []
        self = dff_exporter

        for b_material in obj.data.materials:

            if b_material is None:
                continue
            
            material = dff.Material()
            helper = material_helper(b_material)

            material.color             = helper.get_base_color()
            material.surface_properties = helper.get_surface_properties()
            
            texture = helper.get_texture()
            if texture:
                material.textures.append(texture)

            # Materials
            material.add_plugin('bump_map', helper.get_normal_map())
            material.add_plugin('env_map', helper.get_environment_map())
            material.add_plugin('spec', helper.get_specular_material())
            material.add_plugin('refl', helper.get_reflection_material())
            material.add_plugin('udata', helper.get_user_data())

            anim = helper.get_uv_animation()
            if anim:
                material.add_plugin('uv_anim', anim.name)
                self.dff.uvanim_dict.append(anim)
                
            materials.append(material)
                
        return materials

    #######################################################
    @staticmethod
    def get_skin_plg_and_bone_groups(obj, mesh):

        # Returns a SkinPLG object if the object has an armature modifier
        armature = None
        for modifier in obj.modifiers:
            if modifier.type == 'ARMATURE':
                armature = modifier.object
                break
            
        if armature is None:
            return (None, {})
        
        skin = dff.SkinPLG()
        
        bones = armature.data.bones
        skin.num_bones = len(bones)

        bone_groups = {} # This variable will store the bone groups
                         # to export keyed by their indices
                         
        for index, bone in enumerate(bones):
            matrix = bone.matrix_local.inverted().transposed()
            skin.bone_matrices.append(
                matrix
            )
            try:
                bone_groups[obj.vertex_groups[bone.name].index] = index

            except KeyError:
                pass
            
        return (skin, bone_groups)

    #######################################################
    @staticmethod
    def get_vertex_shared_loops(vertex, layers_list, funcs):
        #temp = [[None] * len(layers) for layers in layers_list]
        shared_loops = {}

        for loop in vertex.link_loops:
            start_loop = vertex.link_loops[0]
            
            shared = False
            for i, layers in enumerate(layers_list):
               
                for layer in layers:

                    if funcs[i](start_loop[layer], loop[layer]):
                        shared = True
                        break

                if shared:
                    shared_loops[loop] = True
                    break
                
        return shared_loops.keys()

    #######################################################
    @staticmethod
    def triangulate_mesh(mesh):
        bm = bmesh.new()
        bm.from_mesh(mesh)
        bmesh.ops.triangulate(bm, faces=bm.faces)
        bm.to_mesh(mesh)
        bm.free()

    #######################################################
    @staticmethod
    def find_vert_idx_by_tmp_idx(verts, idx):
        for i, vert in enumerate(verts):
            if vert['tmp_idx'] == idx:
                return i
        
    #######################################################
    @staticmethod
    def cleanup_duplicate_verts(obj, verts, faces):
        self = dff_exporter
        removed_verts = {}
        i = 0
        
        while i < len(verts):
            vert = verts[i]

            j = i+1
            while j < len(verts):
                vert2 = verts[j]

                # We don't check all properties here because the other properties
                # are vertex-based, so they're guaranteed to be equal if the idx
                # property is equal.
                if vert['idx'] == vert2['idx'] and \
                   vert['normal'] == vert2['normal'] and \
                   vert['uvs'] == vert2['uvs']:
                    # Remove vertex and store the other in the map to change in face
                    removed_verts[vert2['tmp_idx']] = vert['tmp_idx']
                    del verts[j]
                else:
                    j += 1

            i += 1

        # update indices in faces
        for face in faces:
            for i, vert_idx in enumerate(face['verts']):
                if vert_idx in removed_verts:
                    face['verts'][i] = self.find_vert_idx_by_tmp_idx(
                        verts, removed_verts[vert_idx])
                else:
                    face['verts'][i] = self.find_vert_idx_by_tmp_idx(verts, vert_idx)

    #######################################################
    @staticmethod
    def populate_geometry_from_vertices_data(vertices_list, skin_plg, mesh, obj, geometry):

        has_prelit_colors = len(mesh.vertex_colors) > 0 and obj.dff.day_cols
        has_night_colors  = len(mesh.vertex_colors) > 1 and obj.dff.night_cols

        # This number denotes what the maximum number of uv maps exported will be.
        # If obj.dff.uv_map2 is set (i.e second UV map WILL be exported), the
        # maximum will be 2. If obj.dff.uv_map1 is NOT set, the maximum cannot
        # be greater than 0.
        max_uv_layers = (obj.dff.uv_map2 + 1) * obj.dff.uv_map1
        max_uv_layers = (obj.dff.uv_map2 + 1) * obj.dff.uv_map1

        extra_vert = None
        if has_night_colors:
            extra_vert = dff.ExtraVertColorExtension([])
        
        for vertex in vertices_list:
            geometry.vertices.append(dff.Vector._make(vertex['co']))
            geometry.normals.append(dff.Vector._make(vertex['normal']))

            # vcols
            #######################################################
            if has_prelit_colors:
                geometry.prelit_colors.append(dff.RGBA._make(
                    int(col * 255) for col in vertex['vert_cols'][0]))
            if has_night_colors:
                extra_vert.colors.append(dff.RGBA._make(
                    int(col * 255) for col in vertex['vert_cols'][1]))

            # uv layers
            #######################################################
            for index, uv in enumerate(vertex['uvs']):
                if index >= max_uv_layers:
                    break

                while index >= len(geometry.uv_layers):
                    geometry.uv_layers.append([])

                geometry.uv_layers[index].append(dff.TexCoords(uv.x, 1-uv.y))

            # bones
            #######################################################
            if skin_plg is not None:
                skin_plg.vertex_bone_indices.append([0,0,0,0])
                skin_plg.vertex_bone_weights.append([0,0,0,0])

                for index, bone in enumerate(vertex['bones']):
                    skin_plg.vertex_bone_indices[-1][index] = bone[0]
                    skin_plg.vertex_bone_weights[-1][index] = bone[1]

        if skin_plg is not None:
            geometry.extensions['skin'] = skin_plg
        if extra_vert:
            geometry.extensions['extra_vert_color'] = extra_vert

    #######################################################
    @staticmethod
    def populate_geometry_from_faces_data(faces_list, geometry):
        for face in faces_list:
            verts = face['verts']
            geometry.triangles.append(
                dff.Triangle._make((
                    verts[1], #b
                    verts[0], #a
                    face['mat_idx'], #material
                    verts[2] #c
                ))
            )
                    
    #######################################################
    @staticmethod
    def populate_geometry_with_mesh_data(obj, geometry):
        self = dff_exporter

        mesh = self.convert_to_mesh(obj)
        self.transfer_color_attributes_to_vertex_colors(mesh)
        
        self.triangulate_mesh(mesh)
        mesh.calc_normals_split()

        vertices_list = []
        faces_list = []

        skin_plg, bone_groups = self.get_skin_plg_and_bone_groups(obj, mesh)

        for idx, polygon in enumerate(mesh.polygons):
            faces_list.append(
                {"verts": [idx*3, idx*3+1, idx*3+2],
                 "mat_idx": polygon.material_index})
            
            for loop_index in polygon.loop_indices:
                loop = mesh.loops[loop_index]
                vertex = mesh.vertices[loop.vertex_index]
                uvs = []
                vert_cols = []
                bones = []

                for uv_layer in mesh.uv_layers:
                    uvs.append(uv_layer.data[loop_index].uv)

                for vert_col in mesh.vertex_colors:
                    vert_cols.append(vert_col.data[loop_index].color)

                for group in vertex.groups:
                    # Only upto 4 vertices per group are supported
                    if len(bones) >= 4:
                        break

                    if group.group in bone_groups and group.weight > 0:
                        bones.append((bone_groups[group.group], group.weight))
                        
                vertices_list.append({"idx": loop.vertex_index,
                                      "tmp_idx": len(vertices_list), # for making cleanup convenient later 
                                      "co": vertex.co,
                                      "normal": loop.normal,
                                      "uvs": uvs,
                                      "vert_cols": vert_cols,
                                      "bones": bones})

        self.cleanup_duplicate_verts (obj, vertices_list, faces_list)

        self.populate_geometry_from_vertices_data(
            vertices_list, skin_plg, mesh, obj, geometry)

        self.populate_geometry_from_faces_data(faces_list, geometry)
        
    
    #######################################################
    @staticmethod
    def convert_to_mesh(obj):

        """ 
        A Blender 2.8 <=> 2.7 compatibility function for bpy.types.Object.to_mesh
        """
        
        # Temporarily disable armature
        disabled_modifiers = []
        for modifier in obj.modifiers:
            if modifier.type == 'ARMATURE':
                modifier.show_viewport = False
                disabled_modifiers.append(modifier)

        if bpy.app.version < (2, 80, 0):
            mesh = obj.to_mesh(bpy.context.scene, True, 'PREVIEW')
        else:
            
            depsgraph   = bpy.context.evaluated_depsgraph_get()
            object_eval = obj.evaluated_get(depsgraph)
            mesh        = object_eval.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
            

        # Re enable disabled modifiers
        for modifier in disabled_modifiers:
            modifier.show_viewport = True

        return mesh
    
    #######################################################
    @staticmethod
    def transfer_color_attributes_to_vertex_colors(mesh):
        if bpy.app.version < (3, 2, 0):
            return

        vertex_map = defaultdict(list)
        for poly in mesh.polygons:
            for v_ix, l_ix in zip(poly.vertices, poly.loop_indices):
                vertex_map[v_ix].append(l_ix)

        range_end = len(mesh.color_attributes)
        range_end = range_end > 2 and 2 or range_end
        for index in range( range_end ):
            color_attr = mesh.color_attributes[index]
            if len(mesh.vertex_colors) < (index + 1):
                mesh.vertex_colors.new()
            color_layer = mesh.vertex_colors[index]
            for vert_idx, loop_indices in vertex_map.items():
                the_color = [float(v) for v in color_attr.data[vert_idx].color]
                for loop in loop_indices:
                    setattr(color_layer.data[loop], "color", the_color)
    
    #######################################################
    def populate_atomic(obj):
        self = dff_exporter

        # Create geometry
        geometry = dff.Geometry()
        self.populate_geometry_with_mesh_data (obj, geometry)
        self.create_frame(obj)

        # Bounding sphere
        sphere_center = 0.125 * sum(
            (mathutils.Vector(b) for b in obj.bound_box),
            mathutils.Vector()
        )
        sphere_center = self.multiply_matrix(obj.matrix_world, sphere_center)
        sphere_radius = 1.732 * max(*obj.dimensions) / 2        
        
        geometry.bounding_sphere = dff.Sphere._make(
            list(sphere_center) + [sphere_radius]
        )

        geometry.surface_properties = (0,0,0)
        geometry.materials = self.generate_material_list(obj)

        geometry.export_flags['export_normals'] = obj.dff.export_normals
        geometry.export_flags['write_mesh_plg'] = obj.dff.export_binsplit
        geometry.export_flags['light'] = obj.dff.light
        geometry.export_flags['modulate_color'] = obj.dff.modulate_color
        
        if "dff_user_data" in obj.data:
            geometry.extensions['user_data'] = dff.UserData.from_mem(
                obj.data['dff_user_data'])

        try:
            if obj.dff.pipeline != 'NONE':
                if obj.dff.pipeline == 'CUSTOM':
                    geometry.pipeline = int(obj.dff.custom_pipeline, 0)
                else:
                    geometry.pipeline = int(obj.dff.pipeline, 0)
                    
        except ValueError:
            print("Invalid (Custom) Pipeline")
            
        # Add Geometry to list
        self.current_clump.geometry_list.append(geometry)
        
        # Create Atomic from geometry and frame
        geometry_index = len(self.current_clump.geometry_list) - 1
        frame_index    = len(self.current_clump.frame_list) - 1
        atomic         = dff.Atomic._make((frame_index,
                                           geometry_index,
                                           0x4,
                                           0
        ))
        self.current_clump.atomic_list.append(atomic)

    #######################################################
    @staticmethod
    def calculate_parent_depth(obj):
        parent = obj.parent
        depth = 0
        
        while parent is not None:
            parent = parent.parent
            depth += 1

        return depth        

    #######################################################
    @staticmethod
    def check_armature_parent(obj):

        # This function iterates through all modifiers of the parent's modifier,
        # and check if its parent has an armature modifier set to obj.
        
        for modifier in obj.parent.modifiers:
            if modifier.type == 'ARMATURE':
                if modifier.object == obj:
                    return True

        return False
    
    #######################################################
    @staticmethod
    def export_armature(obj):
        self = dff_exporter
        
        for index, bone in enumerate(obj.data.bones):

            # Create a special bone (contains information for all subsequent bones)
            if index == 0:
                frame = self.create_frame(bone, False)

                # set the first bone's parent to armature's parent
                if obj.parent is not None:
                    frame.parent = self.frames[obj.parent.name]

                bone_data = dff.HAnimPLG()
                bone_data.header = dff.HAnimHeader(
                    0x100,
                    bone["bone_id"],
                    len(obj.data.bones)
                )
                
                # Make bone array in the root bone
                for _index, _bone in enumerate(obj.data.bones):
                    bone_data.bones.append(
                        dff.Bone(
                                _bone["bone_id"],
                                _index,
                                _bone["type"])
                    )

                frame.bone_data = bone_data
                self.current_clump.frame_list.append(frame)
                continue

            # Create a regular Bone
            frame = self.create_frame(bone, False)

            # Set bone data
            bone_data = dff.HAnimPLG()
            bone_data.header = dff.HAnimHeader(
                0x100,
                bone["bone_id"],
                0
            )
            frame.bone_data = bone_data
            self.current_clump.frame_list.append(frame)
        
    #######################################################
    @staticmethod
    def export_objects(objects, name=None):
        self = dff_exporter
        
        self.dff = dff.dff()

        # Skip empty collections
        if len(objects) < 1:
            return
        
        for obj in objects:

            if obj.dff.clump not in self.clumps:
                self.clumps[obj.dff.clump] = dff.Clump()

            self.current_clump = self.clumps[obj.dff.clump]

            # create atomic in this case
            if obj.type == "MESH":
                self.populate_atomic(obj)

            # create an empty frame
            elif obj.type == "EMPTY":
                self.create_frame(obj)

            elif obj.type == "ARMATURE":
                self.export_armature(obj)                    

        # Append all exported clumps
        for clump_idx in sorted(self.clumps.keys()):
            print(len(self.clumps[clump_idx].frame_list))
            self.dff.clumps.append(self.clumps[clump_idx])

        # Collision
        if self.export_coll:
            mem = export_col({
                'file_name'     : name if name is not None else
                               os.path.basename(self.file_name),
                'memory'        : True,
                'version'       : 3,
                'collection'    : self.collection,
                'only_selected' : self.selected,
                'mass_export'   : False
            })

            if len(mem) != 0:
               self.dff.clumps[0].collisions = [mem]

        if name is None:
            self.dff.write_file(self.file_name, self.version )
        else:
            self.dff.write_file("%s/%s" % (self.path, name), self.version)

    #######################################################
    @staticmethod
    def is_selected(obj):
        if bpy.app.version < (2, 80, 0):
            return obj.select
        return obj.select_get()
            
    #######################################################
    @staticmethod
    def export_dff(filename):
        self = dff_exporter

        self.file_name = filename
        self.clumps = {}

        objects = {}
        
        # Export collections
        if bpy.app.version < (2, 80, 0):
            collections = [bpy.data]

        else:
            root_collection = bpy.context.scene.collection
            collections = root_collection.children.values() + [root_collection]
            
        for collection in collections:
            for obj in collection.objects:
                    
                if not self.selected or obj.select_get():
                    objects[obj] = self.calculate_parent_depth(obj)

            if self.mass_export:
                objects = sorted(objects, key=objects.get)
                self.export_objects(objects,
                                    collection.name)
                objects     = {}
                self.frames = {}
                self.bones  = {}
                self.clumps = {}
                self.collection = collection

        if not self.mass_export:
                
            objects = sorted(objects, key=objects.get)
            self.export_objects(objects)
                
#######################################################
def export_dff(options):

    # Shadow Function
    dff_exporter.selected    = options['selected']
    dff_exporter.mass_export = options['mass_export']
    dff_exporter.path        = options['directory']
    dff_exporter.version     = options['version']
    dff_exporter.export_coll = options['export_coll']

    dff_exporter.export_dff(options['file_name'])
