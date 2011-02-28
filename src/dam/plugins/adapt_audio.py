from variants.models import Variant
def inspect(workspace):
    from django.db.models import Q
    variants = [[variant.name] for variant in Variant.objects.filter(Q(workspace = workspace) | Q(workspace__isnull = True),  hidden = False)]
#    source_variants = [[variant.name] for variant in Variant.objects.filter(Q(workspace = workspace) | Q(workspace__isnull = True), auto_generated = False)]
#    output_variants = [[variant.name] for variant in Variant.objects.filter(Q(workspace = workspace) | Q(workspace__isnull = True), auto_generated = True, hidden = False)]
     
    width = 200
    audio_rate = {
                  'xtype': 'numberfield',
                  'fieldLabel': 'Sample Rate',
                  'name': 'audio_rate',                  
                  'width': 200
    }
    
    return {
        'name': __name__,
       
        
        'params':[
            {   
                'name': 'source_variant',
                'fieldLabel': 'Source Rendition',
                'xtype': 'select',
                'values': variants,
                'description': 'input-variant',
                
                'help': ''
            },
            
            {   
                'name': 'output_variant',
                'fieldLabel': 'Output Rendition',
                'xtype': 'select',
                'values': variants,
                'description': 'output-variant',
                'default': 0,
                'help': ''
            },
             
                {
                 'xtype':'selectfieldset',
                 'fieldLabel': 'Name',
                 'title': 'Preset',
                 'select_name': 'preset',
                 'values':{
                    'MP3':[
                           audio_rate,
                           {
                        'xtype': 'numberfield',
                        'name': 'audio_bitrate_kb',    
                        'fieldLabel': 'Bit Rate  (Kbps)',
                        'width': width
                    }],
                    
                    'AAC':[
                           audio_rate,
                           {
                        'xtype': 'numberfield',
                        'name': 'audio_bitrate_b',    
                        'fieldLabel': 'Bit Rate (bps)',
                        'width': width
                    }],
                    
                    'OGG':[
                           audio_rate,
                           {
                        'xtype': 'numberfield',
                        'name': 'audio_quality',    
                        'fieldLabel': 'Audio Quality',
                        'minValue': 0,
                        'maxValue': 1,
                        'allowsDecimal': True,                    
                        'width': width
                    }],
                    'WAV': [audio_rate]
                    
                      
                }
                 
             }
                      
            
        ]
                    
    } 
