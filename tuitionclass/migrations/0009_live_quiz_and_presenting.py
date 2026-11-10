# Live room: quiz push (LivePoll correct option + reveal) and present PDF/slides (ClassSession).

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('tuitionclass', '0008_live_guest_and_qa_upvotes'),
    ]

    operations = [
        migrations.AddField(
            model_name='livepoll',
            name='correct_option_index',
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='livepoll',
            name='explanation',
            field=models.CharField(blank=True, default='', max_length=300),
        ),
        migrations.AddField(
            model_name='livepoll',
            name='revealed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='classsession',
            name='presented_material',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='tuitionclass.classmaterial'),
        ),
        migrations.AddField(
            model_name='classsession',
            name='presented_page',
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name='classsession',
            name='presented_page_count',
            field=models.PositiveIntegerField(default=0),
        ),
    ]
