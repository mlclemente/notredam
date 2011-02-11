import mimetypes
from dam.metadata.models import MetadataProperty, MetadataValue

def save_type(self, ctype, component):
    "Extract and save the format of the component as the value of dc:format"
    mime_type = mimetypes.guess_type(component._id)[0]
    component.format = mime_type.split('/')[1]
    metadataschema_mimetype = MetadataProperty.objects.get(namespace__prefix='dc',field_name='format')
    MetadataValue.objects.create(schema=metadataschema_mimetype, content_object=component, value=mime_type)

